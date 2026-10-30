import { FeedManager } from "@/components/feed-manager";
import { MAX_CONSOLE_FEEDS } from "@/lib/console";
import type { SetupController } from "../controller";
import styles from "../setup.module.css";

export function CamerasStep({ c }: { c: SetupController }) {
  const { setup, setFeeds } = c;
  return (
    <section>
      <h1 className={styles.title}>Which views should exist?</h1>
      <p className={styles.lede}>
        Pair cameras here in the browser. The console opens these same feeds each time it
        starts. A local recording is stored only in this browser; a device camera uses the
        browser&apos;s own permission.
      </p>

      <div className={styles.sectionCard}>
        <div className={styles.sectionHead}>
          <div>
            <h2 className={styles.sectionTitle}>Camera inputs</h2>
            <p className={styles.sectionNote}>
              Give each view a name and a room. &ldquo;Loop&rdquo; replays a recording continuously,
              which is useful for a lab feed and wrong for a real event.
            </p>
          </div>
          <span className={styles.count}>{setup.feeds.length} / {MAX_CONSOLE_FEEDS}</span>
        </div>
        <FeedManager feeds={setup.feeds} onChange={setFeeds} limit={MAX_CONSOLE_FEEDS} />
      </div>

      <div className={styles.privacyLine}>
        <span aria-hidden="true">◇</span>
        <p>
          <strong>No pairing code, no cloud account.</strong> A browser camera is paired by
          permission alone. The one exception is a private room: do not point a camera into a
          bathroom or changing area, and mask it at the camera if it can see one.
        </p>
      </div>
    </section>
  );
}
