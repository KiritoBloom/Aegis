"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { Zone } from "@/lib/api";

/* ---------------------------------------------------------------------------
   Icons. Hand-drawn 1.5px strokes on a 20px grid: a care product should not
   look like it shipped with an icon font, and at this weight they sit calmly
   next to 16px text instead of shouting over it.
--------------------------------------------------------------------------- */
type IconName =
  | "today"
  | "alerts"
  | "live"
  | "privacy"
  | "profile"
  | "sim"
  | "arrow"
  | "check"
  | "back"
  | "chevron"
  | "pause"
  | "door"
  | "camera"
  | "shield";

const PATHS: Record<IconName, React.ReactNode> = {
  today: (
    <>
      <path d="M3 9.5 10 3l7 6.5" />
      <path d="M5 8.5V17h10V8.5" />
    </>
  ),
  alerts: (
    <>
      <path d="M10 3.5c-2.6 0-4.2 2-4.2 4.6 0 3.4-1.3 4.4-1.3 4.4h11s-1.3-1-1.3-4.4C14.2 5.5 12.6 3.5 10 3.5Z" />
      <path d="M8.4 15.2a1.7 1.7 0 0 0 3.2 0" />
    </>
  ),
  live: (
    <>
      <rect x="2.5" y="5.5" width="11" height="9" rx="2" />
      <path d="M13.5 9.5 17.5 7v6l-4-2.5Z" />
    </>
  ),
  privacy: (
    <>
      <path d="M10 2.8 4 5v5c0 3.3 2.5 5.9 6 7.2 3.5-1.3 6-3.9 6-7.2V5l-6-2.2Z" />
      <path d="M7.6 9.9 9.4 12l3.2-3.6" />
    </>
  ),
  profile: (
    <>
      <circle cx="10" cy="7" r="2.9" />
      <path d="M4.4 16.5c.6-2.8 2.8-4.3 5.6-4.3s5 1.5 5.6 4.3" />
    </>
  ),
  sim: (
    <>
      <path d="M4 6.5h12M4 10h12M4 13.5h7" />
      <circle cx="14.5" cy="13.5" r="1.4" />
    </>
  ),
  arrow: <path d="M4 10h11m-4.2-4.2L15 10l-4.2 4.2" />,
  check: <path d="M4.5 10.5 8 14l7.5-8" />,
  back: <path d="M15 10H4m4.2-4.2L4 10l4.2 4.2" />,
  chevron: <path d="M8 5l5 5-5 5" />,
  pause: (
    <>
      <rect x="6" y="4.5" width="2.6" height="11" rx="1" />
      <rect x="11.4" y="4.5" width="2.6" height="11" rx="1" />
    </>
  ),
  door: (
    <>
      <path d="M4 17V4.5h8V17" />
      <path d="M12 17h4" />
      <circle cx="9.6" cy="10.5" r="0.7" />
    </>
  ),
  camera: (
    <>
      <path d="M3 6.5h9v7H3z" />
      <path d="M12 9.2 17 7v6l-5-2.2Z" />
      <path d="M3 17h14" />
    </>
  ),
  shield: (
    <>
      <path d="M10 2.8 4 5v5c0 3.3 2.5 5.9 6 7.2 3.5-1.3 6-3.9 6-7.2V5l-6-2.2Z" />
    </>
  ),
};

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flex: "none" }}
    >
      {PATHS[name]}
    </svg>
  );
}

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

export function SeverityBar({ severity }: { severity: string }) {
  const color =
    severity === "emergency" || severity === "urgent"
      ? "var(--color-red)"
      : severity === "watch"
        ? "var(--color-amber)"
        : "var(--color-sage)";
  return <span className="sev-bar" style={{ background: color }} aria-hidden="true" />;
}

/* ---------------------------------------------------------------------------
   The floor plan. A top-down sketch, not a camera view: it answers "which part
   of the house" in one glance, which a fisheye frame never does.
--------------------------------------------------------------------------- */
export function FloorPlan({
  zones,
  activeZone,
  residentZone,
  height = 260,
}: {
  zones: Zone[];
  activeZone?: string | null;
  residentZone?: string | null;
  height?: number;
}) {
  // The config's coordinates are normalised against the camera frame, which is
  // a perspective view. Mapped onto a flat plan they are still the right
  // relative arrangement, which is all a schematic needs to be.
  const pad = 14;
  const w = 340;
  const h = 220;
  const sx = (x: number) => pad + x * (w - pad * 2);
  const sy = (y: number) => pad + y * (h - pad * 2);

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      style={{ width: "100%", height, display: "block" }}
      role="img"
      aria-label={`Floor plan with ${zones.length} watched areas`}
    >
      <defs>
        <pattern id="grid" width="17" height="17" patternUnits="userSpaceOnUse">
          <path d="M17 0H0V17" fill="none" stroke="#e9e3d9" strokeWidth="1" />
        </pattern>
      </defs>
      <rect x="0" y="0" width={w} height={h} fill="url(#grid)" />
      {zones.map((z) => {
        const pts = z.points.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ");
        const active = activeZone === z.name;
        const hasResident = residentZone === z.name;
        const fill = z.private
          ? "#2a2f34"
          : active
            ? "rgba(169,59,50,0.14)"
            : hasResident
              ? "rgba(95,127,106,0.16)"
              : "rgba(20,24,28,0.035)";
        const stroke = z.private ? "#2a2f34" : active ? "var(--color-red)" : "var(--color-line-2)";
        return (
          <g key={z.name}>
            <polygon
              points={pts}
              fill={fill}
              stroke={stroke}
              strokeWidth={active ? 1.8 : 1}
              strokeDasharray={z.private ? "3 3" : undefined}
            />
            <text
              x={sx(z.points.reduce((a, p) => a + p[0], 0) / z.points.length)}
              y={sy(z.points.reduce((a, p) => a + p[1], 0) / z.points.length)}
              textAnchor="middle"
              fontSize="9.5"
              fontFamily="var(--font-mono)"
              fill={z.private ? "#f6f3ed" : "var(--color-ink-3)"}
              letterSpacing="0.04em"
            >
              {z.private ? "not watched" : z.name.replace(/_/g, " ")}
            </text>
          </g>
        );
      })}
    </svg>
  );
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
  { href: "/", label: "Today", icon: "today" as IconName },
  { href: "/alerts", label: "Alerts", icon: "alerts" as IconName },
  { href: "/live", label: "Camera", icon: "live" as IconName },
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
