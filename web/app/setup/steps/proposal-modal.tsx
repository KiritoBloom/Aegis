import { MODE_LABEL, type SetupController } from "../controller";
import styles from "../setup.module.css";

export function ProposalModal({ c }: { c: SetupController }) {
  const { setup, classes, museApplied, setShowProposal, approveProposal, rejectProposal, changeMuseChoices } = c;
  const { proposal } = setup;
  if (!proposal) return null;
  return (
    <div className={styles.modalOverlay} role="presentation" onClick={(event) => {
      if (event.target === event.currentTarget) setShowProposal(false);
    }}>
      <div className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="muse-proposal-title">
        <p className={styles.kicker}>Muse · proposed configuration</p>
        <h2 id="muse-proposal-title">Here is how Aegis would watch.</h2>
        <p className={styles.modalSummary}>{proposal.summary}</p>
        {proposal.notes.length ? (
          <ul className={styles.modalNotes}>
            {proposal.notes.map((note) => <li key={note}>{note}</li>)}
          </ul>
        ) : null}
        <div className={styles.modalModes}>
          {classes.map((spec) => {
            const mode = proposal.modes[spec.id] ?? spec.default;
            return (
              <div key={spec.id} className={styles.modalMode}>
                <span>{spec.label}{spec.locked ? " · locked" : ""}</span>
                <span>{MODE_LABEL[mode] ?? mode}</span>
              </div>
            );
          })}
        </div>
        <p className={styles.modalNote}>
          Safety classes stay on alert. Every choice can be changed later in Settings. Approving
          applies these modes to the household plan; nothing is activated until the console is
          switched on.
        </p>
        <div className={styles.modalActions}>
          {setup.configApproved && museApplied ? (
            <>
              <button type="button" className={styles.primary} onClick={() => setShowProposal(false)}>
                Close
              </button>
              <span className={styles.spacer} />
              <button
                type="button"
                className={styles.quiet}
                onClick={changeMuseChoices}
              >
                Change these choices
              </button>
            </>
          ) : (
            <>
              <button type="button" className={styles.primary} onClick={() => void approveProposal()}>
                Approve configuration
              </button>
              <button type="button" className={styles.secondary} onClick={rejectProposal}>
                Disapprove
              </button>
              <span className={styles.spacer} />
              <button type="button" className={styles.quiet} onClick={() => setShowProposal(false)}>
                Review later
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
