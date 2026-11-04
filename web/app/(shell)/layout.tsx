import "../legacy.css";
import { ConsoleShell } from "@/components/console-shell";
import shared from "@/components/console-shared.module.css";

/** Legacy shell for secondary pages. The camera console at `/` is the primary surface. */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConsoleShell><div className={`${shared.page} legacy-page`}>{children}</div></ConsoleShell>
  );
}
