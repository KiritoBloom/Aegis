"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { Zone } from "@/lib/api";
import { Icon, type IconName } from "./icons";

/* ---------------------------------------------------------------------------
   Severity. Never colour alone — every chip carries its word, and the bar has
   a shape (width) as well as a hue.
--------------------------------------------------------------------------- */
export function SeverityChip({ severity }: { severity: string }) {
  const tone =
    severity === "emergency" || severity === "urgent"
      ? "chip-red"
      : severity === "watch"
        ? "chip-amber"
        : "chip-sage";
  const word =
    severity === "emergency"
      ? "Emergency"
      : severity === "urgent"
        ? "Urgent"
        : severity === "watch"
          ? "Worth a look"
          : "Noted";
  return <span className={`chip ${tone}`}>{word}</span>;
}


/* ---------------------------------------------------------------------------
   The live frame with zones drawn on it. This is the single most "CCTV" element
   in the product: the same pixels the detector sees, with the geometry the rules
   use, in the same coordinate space. Masks are painted opaque — proof, not
   decoration.
--------------------------------------------------------------------------- */
export function CameraFrame({
  src,
  zones,
  activeZone,
  caption,
}: {
  src: string | null;
  zones: Zone[];
  activeZone?: string | null;
  caption?: React.ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setFailed(false);
    setLoaded(false);
  }, [src]);

  return (
    <div
      style={{
        position: "relative",
        borderRadius: 12,
        overflow: "hidden",
        background: "#171b1f",
        aspectRatio: "4 / 3",
      }}
    >
      {src && !failed ? (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={src}
          alt="The most recent frame Aegis kept, with the watched areas drawn over it"
          onError={() => setFailed(true)}
          onLoad={() => setLoaded(true)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            display: "block",
            opacity: loaded ? 1 : 0,
            transition: "opacity 240ms ease-out",
          }}
        />
      ) : (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "grid",
            placeItems: "center",
            color: "#8b949c",
            fontSize: 13,
          }}
        >
          {failed ? "No frame available" : "Loading the last frame…"}
        </div>
      )}
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
        aria-hidden="true"
      >
        {zones.map((z) => {
          const pts = z.points.map(([x, y]) => `${x * 100},${y * 100}`).join(" ");
          const active = activeZone === z.name;
          return (
            <polygon
              key={z.name}
              points={pts}
              fill={z.private ? "#000" : active ? "rgba(169,59,50,0.18)" : "rgba(255,255,255,0.02)"}
              stroke={z.private ? "#000" : active ? "#e8b4ae" : "rgba(246,243,237,0.45)"}
              strokeWidth={active ? 0.5 : 0.3}
              strokeDasharray={z.private ? undefined : "1.4 1.4"}
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>
      {caption ? (
        <div
          style={{
            position: "absolute",
            left: 10,
            bottom: 10,
            right: 10,
            display: "flex",
            gap: 8,
            alignItems: "center",
            justifyContent: "space-between",
            fontFamily: "var(--font-mono)",
            fontSize: 10.5,
            letterSpacing: "0.04em",
            color: "#f6f3ed",
            textShadow: "0 1px 3px rgba(0,0,0,0.7)",
          }}
        >
          {caption}
        </div>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   States from plan §12.7. Written once so no page can invent its own wording.
--------------------------------------------------------------------------- */
export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div style={{ padding: "34px 4px", textAlign: "left" }}>
      <p className="h2" style={{ margin: 0 }}>{title}</p>
      {children ? <div className="small" style={{ marginTop: 6, maxWidth: 460 }}>{children}</div> : null}
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%", r = 6 }: { h?: number; w?: number | string; r?: number }) {
  return <div className="skel" style={{ height: h, width: w, borderRadius: r }} />;
}

export function Banner({
  tone,
  children,
  action,
}: {
  tone: "amber" | "red" | "sage";
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  const map = {
    amber: { bg: "var(--color-amber-soft)", bd: "#e6d2ac", fg: "#7d5110" },
    red: { bg: "var(--color-red-soft)", bd: "#eccbc6", fg: "#8a2c24" },
    sage: { bg: "var(--color-sage-soft)", bd: "#c6d4c6", fg: "#3f5c48" },
  }[tone];
  return (
    <div
      role={tone === "red" ? "alert" : "status"}
      style={{
        display: "flex",
        gap: 12,
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
        background: map.bg,
        border: `1px solid ${map.bd}`,
        color: map.fg,
        borderRadius: 10,
        padding: "10px 14px",
        fontSize: 14,
      }}
    >
      <span>{children}</span>
      {action}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   The rail (desktop) and tab bar (mobile). Same links, one component each.
--------------------------------------------------------------------------- */
const LINKS = [
  { href: "/", label: "Console", icon: "grid" as IconName },
  { href: "/night", label: "Night", icon: "night" as IconName },
  { href: "/live", label: "Edge camera", icon: "live" as IconName },
  { href: "/privacy", label: "Privacy", icon: "privacy" as IconName },
];

const SECONDARY = [
  { href: "/profile", label: "Household", icon: "profile" as IconName },
  { href: "/sim", label: "Control room", icon: "sim" as IconName },
];

export function NavRail() {
  const path = usePathname();
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <nav aria-label="Main" style={{ display: "flex", flexDirection: "column", gap: 26 }}>
      <Link
        href="/"
        style={{ textDecoration: "none", color: "inherit", display: "block", padding: "0 10px" }}
      >
        <span className="serif" style={{ fontSize: 26, letterSpacing: "-0.03em" }}>
          Aegis
        </span>
        <span
          className="mono"
          style={{ display: "block", fontSize: 10.5, color: "var(--color-ink-3)", letterSpacing: "0.08em" }}
        >
          MARGARET&apos;S HOME
        </span>
      </Link>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} className="navlink" data-active={active(l.href)}>
            <Icon name={l.icon} size={18} />
            {l.label}
          </Link>
        ))}
      </div>
      <div>
        <p className="eyebrow" style={{ padding: "0 10px", marginBottom: 6 }}>Setup</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {SECONDARY.map((l) => (
            <Link key={l.href} href={l.href} className="navlink" data-active={active(l.href)}>
              <Icon name={l.icon} size={18} />
              {l.label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}

export function TabBar() {
  const path = usePathname();
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const items = [...LINKS, { href: "/profile", label: "Household", icon: "profile" as IconName }];
  return (
    <nav
      aria-label="Main"
      style={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 40,
        display: "grid",
        gridTemplateColumns: `repeat(${items.length}, 1fr)`,
        background: "rgba(251,249,245,0.94)",
        backdropFilter: "blur(12px)",
        borderTop: "1px solid var(--color-line)",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      {items.map((l) => (
        <Link key={l.href} href={l.href} className="tablink" data-active={active(l.href)}>
          <Icon name={l.icon} size={19} />
          {l.label}
        </Link>
      ))}
    </nav>
  );
}

/** A labelled fact row — the workhorse of every detail panel. */
export function Fact({ label, value, mono = true }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 16, padding: "7px 0" }}>
      <span className="micro" style={{ color: "var(--color-ink-3)" }}>{label}</span>
      <span className={mono ? "mono" : ""} style={{ fontSize: 13, textAlign: "right" }}>{value}</span>
    </div>
  );
}
