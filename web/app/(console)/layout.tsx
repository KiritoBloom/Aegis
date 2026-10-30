import type { Viewport } from "next";
import { ConsoleProvider } from "@/components/console-provider";
import { ConsoleShell } from "@/components/console-shell";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  themeColor: "#0b0f0e",
  viewportFit: "cover",
};

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConsoleProvider>
      <ConsoleShell>{children}</ConsoleShell>
    </ConsoleProvider>
  );
}
