import { NavRail, TabBar } from "@/components/ui";

/**
 * The shell.
 *
 * A rail on a laptop, a tab bar on a phone, one set of routes. Plan §12.8 is
 * explicit that there must not be a second mobile UI: the alert page *is* the
 * product on a phone, so it has to be the same code path that is tested.
 */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: "100dvh", display: "flex", flexDirection: "column" }}>
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
          <div style={{ position: "sticky", top: 28 }}>
            <NavRail />
            <div style={{ marginTop: 30, padding: "0 10px" }}>
              <p className="micro" style={{ lineHeight: 1.5 }}>
                One camera. Nothing leaves the house unless something happens.
              </p>
            </div>
          </div>
        </aside>

        <main
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
