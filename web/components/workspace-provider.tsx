"use client";

import { usePathname } from "next/navigation";
import { ConsoleProvider } from "./console-provider";

/** Keep capture alive across family routes; the resident kiosk is isolated. */
export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname.startsWith("/checkin/")) return <>{children}</>;
  return <ConsoleProvider>{children}</ConsoleProvider>;
}
