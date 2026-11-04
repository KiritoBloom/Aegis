import type { SetupController } from "../controller";
import styles from "../setup.module.css";

export function StartStep({ c }: { c: SetupController }) {
  const { setup, go } = c;
  return (
    <section>
      <h1 className={styles.title}>A little preparation.<br />More peace of mind.</h1>
      <p className={styles.lede}>
        Choose your cameras, tell us about your household, and decide when Aegis should ask for your attention.
      </p>

      <div className={styles.sectionCard}>
        <div className={styles.sectionHead}>
          <div>
            <h2 className={styles.sectionTitle}>Set up your household</h2>
            <p className={styles.sectionNote}>
              Setup is flexible: add one camera or six, describe as many people as live here,
              and write your own conditions if the list does not fit.
            </p>
          </div>
          <span className={styles.count}>6 STEPS</span>
        </div>
        <div className={styles.startGrid}>
          {[
            ["01", "Add cameras", "A local recording, a device camera, or a lab clip. Up to six views."],
            ["02", "Describe the household", "Names, ages, and care needs help Aegis understand what deserves attention."],
            ["03", "Map every view", "One frame per camera is checked on this machine. No image leaves the house."],
            ["04", "Approve the plan", "Muse proposes a configuration from labels and answers. You approve or discard it."],
          ].map(([n, title, body]) => (
            <div key={n} className={styles.startItem}>
              <span className={styles.startNumber}>{n}</span>
              <p className={styles.startItemTitle}>{title}</p>
              <p className={styles.startItemBody}>{body}</p>
            </div>
          ))}
        </div>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} onClick={() => go("cameras")}>
            Begin setup
          </button>
          <span className={styles.help}>
            {setup.completed ? "This household is already installed. You can review or change anything." : "Nothing is watched until you activate the console at the end."}
          </span>
        </div>
      </div>

      <div className={styles.privacyLine}>
        <span aria-hidden="true">◇</span>
        <p>
          <strong>Your home stays private.</strong> Setup images are processed locally. You can choose whether Muse reviews your answers and pause monitoring at any time.
        </p>
      </div>
    </section>
  );
}
