import type { Viewport } from "next";
import { ConsoleShell } from "@/components/console-shell";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
   themeColor: "#f7f8f7",
  viewportFit: "cover",
};

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConsoleShell>{children}</ConsoleShell>
  );
}
