"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { useConsole } from "@/components/console-provider";
import { setupReadyToWatch } from "@/lib/console";
import styles from "./console-shell.module.css";

const NAV = [
  { href: "/", label: "Cameras", icon: "grid" as const },
  { href: "/events", label: "Events", icon: "alerts" as const },
  { href: "/logs", label: "Logs", icon: "logs" as const },
  { href: "/settings", label: "Settings", icon: "settings" as const },
];

const TITLES: Record<string, string> = {
  "/": "Live cameras",
  "/events": "Events",
  "/logs": "Per-camera logs",
  "/settings": "Settings",
};

function clockText(date: Date): string {
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const {
    ready, setup, resident, active, setActive, feeds, live,
    toasts, dismissToast, blockedReason, clearBlocked,
  } = useConsole();
  const [clock, setClock] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const residentName = resident?.name?.trim() || "Resident";
  const watching = feeds.filter((feed) => live[feed.id]?.status === "watching").length;
  const readiness = useMemo(() => setupReadyToWatch(setup), [setup]);

  const setupHref = (() => {
    if (!setup || !setup.completed) return "/setup";
    if (!setup.feeds.length) return "/setup?step=cameras";
    if (setup.feeds.some((feed) => !setup.mapping[feed.id])) return "/setup?step=mapping";
    if (!setup.configApproved) return "/setup?step=finish";
    return "/setup";
  })();

  const toggle = async () => {
    await setActive(!active);
  };

  if (!ready) {
    return (
      <div className={styles.boot}>
        <span className={styles.bootMark}>AEGIS</span>
        <span className={styles.bootLine}>Opening the console…</span>
      </div>
    );
  }

  return (
    <div className={styles.app}>
      <a href="#console-main" className="skip-link">Skip to content</a>

      <aside className={styles.side}>
        <Link href="/" className={styles.brand}>
          <span className={styles.wordmark}>Aegis</span>
          <span className={styles.brandMeta}>care console</span>
        </Link>

        <div className={styles.statusBlock} data-active={active}>
          <div className={styles.statusTop}>
            <span className={styles.statusDot} />
            <span>{active ? "Watching" : "Standby"}</span>
            <span className={styles.statusCode}>{active ? "ON" : "OFF"}</span>
          </div>
          <p className={styles.statusName}>{residentName}</p>
          <p className={styles.statusMeta}>
            {watching}/{feeds.length} feeds live · {setup?.mapping ? Object.keys(setup.mapping).length : 0} mapped
          </p>
        </div>

        <nav className={styles.nav} aria-label="Console">
          {NAV.map((item) => {
            const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link key={item.href} href={item.href} className={styles.navLink} data-active={isActive}
                aria-current={isActive ? "page" : undefined}>
                <Icon name={item.icon} size={17} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className={styles.sideFoot}>
          <p>
            Frames stay on this machine. Mapping is local. A clip leaves only when
            a confirmed event needs the family.
          </p>
          <Link href={setupHref} className={styles.sideLink}>
            {readiness.ready ? "Review installation" : "Finish installation"}
          </Link>
        </div>
      </aside>

      <div className={styles.main}>
        <header className={styles.topbar}>
          <div className={styles.crumb}>
            <span className={styles.crumbRoot}>Household</span>
            <span className={styles.crumbSlash}>/</span>
            <span className={styles.crumbTitle}>{TITLES[pathname] ?? "Console"}</span>
          </div>
          <div className={styles.topMeta}>
            <span className={styles.date}>
              {clock.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" }).toUpperCase()}
            </span>
            <span className={styles.clock}>{clockText(clock)}</span>
            <button
              type="button"
              className={styles.activate}
              data-on={active}
              onClick={() => void toggle()}
              role="switch"
              aria-checked={active}
              aria-label={active ? "Deactivate monitoring" : "Activate monitoring"}
            >
              <span className={styles.led} />
              <span>{active ? "System active" : "Activate system"}</span>
            </button>
          </div>
        </header>

        <main id="console-main" className={styles.content}>
          {children}
        </main>
      </div>

      <nav className={styles.mobileNav} aria-label="Console">
        {NAV.map((item) => {
          const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} data-active={isActive}>
              <Icon name={item.icon} size={18} />
              <span>{item.label}</span>
            </Link>
          );
        })}
        <button type="button" data-active={active} onClick={() => void toggle()}
          aria-label={active ? "Deactivate monitoring" : "Activate monitoring"}>
          <Icon name="pause" size={18} />
          <span>{active ? "Pause" : "Activate"}</span>
        </button>
      </nav>

      <div className={styles.toasts} aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div key={toast.id} className={styles.toast} data-tone={toast.tone}>
            <div className={styles.toastHead}>
              <span className={styles.toastTitle}>{toast.title}</span>
              <button type="button" className={styles.toastClose} onClick={() => dismissToast(toast.id)}
                aria-label="Dismiss">
                <Icon name="close" size={14} />
              </button>
            </div>
            {toast.detail ? <p className={styles.toastBody}>{toast.detail}</p> : null}
          </div>
        ))}
      </div>

      {blockedReason ? (
        <div className={styles.overlay} role="presentation">
          <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="gate-title">
            <p className={styles.dialogKicker}>Activation blocked</p>
            <h2 id="gate-title" className={styles.dialogTitle}>This installation is not ready yet.</h2>
            <p className={styles.dialogBody}>{blockedReason}</p>
            <p className={styles.dialogNote}>
              Activation only unlocks after every camera has a local map and the configuration
              Muse prepared has been approved. This is deliberate: an unmapped camera is a camera
              that cannot tell a bed from a floor.
            </p>
            <div className={styles.dialogActions}>
              <button type="button" className={styles.dialogPrimary}
                onClick={() => { clearBlocked(); router.push(setupHref); }}>
                Go to setup
              </button>
              <button type="button" className={styles.dialogSecondary} onClick={clearBlocked}>
                Not now
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
