import type { ReactNode } from "react";

export type IconName =
  | "alerts"
  | "live"
  | "privacy"
  | "profile"
  | "sim"
  | "night"
  | "arrow"
  | "check"
  | "chevron"
  | "pause"
  | "door"
  | "camera"
  | "shield"
  | "grid"
  | "logs"
  | "settings"
  | "expand"
  | "close";

const PATHS: Record<IconName, ReactNode> = {
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
  night: (
    <>
      <path d="M12.8 3.4a6.6 6.6 0 1 0 3.7 9.2 5.5 5.5 0 0 1-3.7-9.2Z" />
      <path d="M15.2 4.1v2.2M14.1 5.2h2.2" />
    </>
  ),
  arrow: <path d="M4 10h11m-4.2-4.2L15 10l-4.2 4.2" />,
  check: <path d="M4.5 10.5 8 14l7.5-8" />,
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
  grid: (
    <>
      <rect x="3" y="3.5" width="5.5" height="5.5" rx="1" />
      <rect x="11.5" y="3.5" width="5.5" height="5.5" rx="1" />
      <rect x="3" y="12" width="5.5" height="5.5" rx="1" />
      <rect x="11.5" y="12" width="5.5" height="5.5" rx="1" />
    </>
  ),
  logs: (
    <>
      <path d="M4 5.5h8M4 10h12M4 14.5h6" />
      <circle cx="15.5" cy="5.5" r="1.2" />
      <circle cx="13.5" cy="14.5" r="1.2" />
    </>
  ),
  settings: (
    <>
      <path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13" />
      <circle cx="7" cy="5.5" r="1.6" fill="currentColor" stroke="none" />
      <circle cx="13" cy="10" r="1.6" fill="currentColor" stroke="none" />
      <circle cx="8" cy="14.5" r="1.6" fill="currentColor" stroke="none" />
    </>
  ),
  expand: (
    <>
      <path d="M3.5 7.5v-4h4M16.5 12.5v4h-4M12.5 3.5h4v4M7.5 16.5h-4v-4" />
    </>
  ),
  close: <path d="m5 5 10 10M15 5 5 15" />,
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
