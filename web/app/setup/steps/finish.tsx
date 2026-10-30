import Link from "next/link";
import { CONDITION_SUGGESTIONS } from "@/lib/console";
import type { SetupController } from "../controller";
import styles from "../setup.module.css";

export function FinishStep({ c }: { c: SetupController }) {
  const {
    setup, counts, museApplied, primary, consentName, agreed, setAgreed,
    finishError, rejectedMuse, setShowProposal, mutate, approveOwnPlan,
  } = c;
  return (
    <section>
      <h1 className={styles.title}>One last look before the house is watched.</h1>
      <p className={styles.lede}>
        This is the configuration that will run. Nothing is live until you finish setup and
        switch the console on; the console blocks activation if anything here is incomplete.
      </p>

      <div className={styles.summaryGrid}>
        <div className={styles.summaryCard}>
          <h3>Household</h3>
          <p>
            {setup.occupants
              .filter((person) => person.name.trim())
              .map((person) => `${person.name.trim()}${person.age ? `, ${person.age}` : ""}`)
              .join(" · ")}
          </p>
          <span>
            {setup.occupants
              .flatMap((person) => person.conditions)
              .map((condition) => CONDITION_SUGGESTIONS.find((item) => item.id === condition)?.label ?? condition)
              .join(", ") || "No conditions reported"}
          </span>
        </div>
        <div className={styles.summaryCard}>
          <h3>Cameras</h3>
          <p>{setup.feeds.map((feed) => feed.name || feed.label).join(" · ")}</p>
          <span>{setup.feeds.length} views · {Object.keys(setup.mapping).length} mapped locally</span>
        </div>
        <div className={styles.summaryCard}>
          <h3>Plan</h3>
          <p>
            {counts.locked} safety always alert · {counts.alert} optional interrupt · {counts.held} held or quiet
          </p>
          <span>
            {setup.configApproved
              ? museApplied ? "Muse's configuration approved" : "Your configuration approved"
              : "Not approved yet"}
          </span>
        </div>
        <div className={styles.summaryCard}>
          <h3>Privacy</h3>
          <p>Local mapping · structured context only</p>
          <span>Clips leave only on an event, deleted in 7 days locally and 1 hour upstream</span>
        </div>
      </div>

      <div className={styles.sectionCard}>
        <div className={styles.sectionHead}>
          <div>
            <h2 className={styles.sectionTitle}>Configuration approval</h2>
            <p className={styles.sectionNote}>
              The plan must be approved before the console can be activated. This is the gate
              that keeps an unmapped or unreviewed system from calling itself ready.
            </p>
          </div>
          <span className={styles.count}>{setup.configApproved ? "APPROVED" : "REQUIRED"}</span>
        </div>

        {setup.configApproved ? (
          <p className={styles.mapResult}>
            <strong>Approved</strong>
            {setup.configApprovedAt
              ? ` · ${new Date(setup.configApprovedAt).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}`
              : ""}
            {museApplied ? " · Muse's proposed modes were applied." : " · your own plan is running."}
          </p>
        ) : (
          <>
            <p className={styles.museText}>
              Muse has not been approved{setup.proposal ? "" : " and no proposal exists"}. You can
              review Muse&apos;s proposal or approve the choices you made yourself.
            </p>
            <div className={styles.mapActions}>
              {setup.proposal ? (
                <button type="button" className={styles.primary} onClick={() => setShowProposal(true)}>
                  Review Muse proposal
                </button>
              ) : null}
              <button type="button" className={setup.proposal ? styles.secondary : styles.primary} onClick={approveOwnPlan}>
                Approve my configuration
              </button>
            </div>
          </>
        )}

        {rejectedMuse ? (
          <p className={styles.modalNote}>
            Muse&apos;s proposal was discarded. Approving here runs your own watch choices.
          </p>
        ) : null}
      </div>

      <div className={styles.consentBox}>
        <p className={styles.label}>Consent</p>
        <p className={styles.sectionNote} style={{ marginTop: 6 }}>
          Consent has to come from the person being cared for, not only from the family.
          Aegis records the name, the time, and the version of the privacy text.
        </p>
        <label className={styles.field} style={{ marginTop: 14 }}>
          <span className={styles.label}>
            {primary?.name.trim() ? `${primary.name.trim()}'s name, as they gave it` : "The resident's name, as they gave it"}
          </span>
          <input
            className={styles.input}
            value={consentName}
            onChange={(event) => mutate((current) => ({ ...current, consentName: event.target.value }))}
            placeholder={primary?.name.trim() || "Margaret"}
            autoComplete="off"
          />
        </label>
        <label className={styles.consentCheck}>
          <input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />
          <span>
            They agreed to be watched under the terms in the privacy notice, including the
            spoken check-in after an event.
          </span>
        </label>
        <p className={styles.modalNote}>
          Read the <Link href="/privacy" style={{ color: "inherit" }}>privacy notice</Link>.
          You can pause the console or delete everything from the system at any time.
        </p>
      </div>

      {finishError ? <div className={styles.error} role="alert">{finishError}</div> : null}
    </section>
  );
}
