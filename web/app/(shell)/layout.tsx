import Link from "next/link";
import "../legacy.css";
import { HousePulse } from "@/components/house-pulse";
import { NavRail, TabBar } from "@/components/ui";

/** Legacy shell for secondary pages. The camera console at `/` is the primary surface. */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: "100dvh", display: "flex", flexDirection: "column" }}>
      <a href="#main" className="skip-link">Skip to content</a>
      <header className="masthead">
        <div className="masthead-inner">
          <Link href="/" className="masthead-brand masthead-brand--mobile">
            <span className="serif">Aegis</span>
            <span className="mono">MARGARET&apos;S HOME</span>
          </Link>
          <span className="masthead-note">Local cameras. Silence by default.</span>
          <HousePulse />
        </div>
      </header>
      <div
        style={{
          display: "flex",
          flex: 1,
          width: "100%",
          maxWidth: 1240,
          margin: "0 auto",
          padding: "0 20px",
          gap: 40,
        }}
      >
        <aside
          className="hidden lg:block"
          style={{ width: 208, flex: "none", paddingTop: 28, paddingBottom: 40 }}
        >
          <div style={{ position: "sticky", top: 74 }}>
            <NavRail />
            <div style={{ marginTop: 30, padding: "0 10px" }}>
              <p className="micro" style={{ lineHeight: 1.5 }}>
                Feeds stay local. Nothing leaves the house unless something happens.
              </p>
            </div>
          </div>
        </aside>

        <main
          id="main"
          style={{
            flex: 1,
            minWidth: 0,
            paddingTop: 20,
            paddingBottom: 96,
          }}
          className="lg:pt-10 lg:pb-16"
        >
          {children}
        </main>
      </div>
      <div className="lg:hidden">
        <TabBar />
      </div>
    </div>
  );
}
