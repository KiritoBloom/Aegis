import type { SetupController } from "../controller";
import styles from "../setup.module.css";

export function MappingStep({ c }: { c: SetupController }) {
  const {
    setup, allMapped, museApplied, scanning, mapError, previews, proposing,
    museError, setShowProposal, missingMap, mapFeed, propose, go,
    setMuseConsent, askMuseAgain,
  } = c;
  return (
    <section>
      <h1 className={styles.title}>Map each view before anything is watched.</h1>
      <p className={styles.lede}>
        Aegis maps one frame from every camera on this machine. That map tells the local
        tracker what normal looks like: a bed, a chair, a doorway. Nothing is sent to Meta
        during this scan.
      </p>

      {!setup.feeds.length ? (
        <div className={styles.sectionCard}>
          <p className={styles.sectionTitle} style={{ fontSize: 20 }}>There is nothing to map yet.</p>
          <p className={styles.sectionNote}>Add at least one camera first.</p>
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={() => go("cameras")}>
              Add a camera
            </button>
          </div>
        </div>
      ) : (
        <div className={styles.mappingGrid}>
          {setup.feeds.map((feed) => {
            const mapping = setup.mapping[feed.id];
            const preview = previews[feed.id];
            const busy = scanning === feed.id;
            return (
              <div key={feed.id} className={styles.mapCard}>
                <div className={styles.mapPreview}>
                  {preview ? (
                    // The frame is captured locally and lived in memory only.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={preview} alt={`Local mapping frame from ${feed.name || feed.label}`} />
                  ) : (
                    <div className={styles.offline}>
                      {mapping ? "MAPPED EARLIER\nREMAP FOR A FRESH LOCAL FRAME" : "NO FRAME YET\nONE LOCAL FRAME IS NEEDED"}
                    </div>
                  )}
                  <span className={styles.mapLive}>
                    {busy ? "SCANNING" : mapping ? "MAPPED" : "UNMAPPED"}
                  </span>
                </div>
                <div className={styles.mapBody}>
                  <p className={styles.mapTitle}>{feed.name || feed.label}</p>
                  <p className={styles.mapLocation}>
                    {feed.location || "No room label"} · {feed.kind}
                  </p>
                  {mapping ? (
                    <>
                      <p className={styles.mapResult}>
                        <strong>{mapping.personCount} {mapping.personCount === 1 ? "person" : "people"}</strong>
                        {" "}detected · {mapping.objects.length ? `${mapping.objects.length} ${mapping.objects.length === 1 ? "object" : "objects"}` : "no scene objects"}
                        {(mapping.flags ?? []).includes("lying") ? " · a lying posture is present" : ""}
                      </p>
                      <div className={styles.mapStatus}>
                        {Array.from(new Set(mapping.objects)).map((object) => (
                          <span key={object} className={styles.objectChip}>{object}</span>
                        ))}
                        {!mapping.objects.length ? <span className={styles.objectChip}>no objects</span> : null}
                      </div>
                    </>
                  ) : (
                    <p className={styles.mapResult}>
                      Not mapped. The console cannot be activated until this view has a local map.
                    </p>
                  )}
                  <div className={styles.mapActions}>
                    <button
                      type="button"
                      className={mapping ? styles.secondary : styles.primary}
                      onClick={() => void mapFeed(feed)}
                      disabled={busy || Boolean(scanning)}
                    >
                      {busy ? "Mapping…" : mapping ? "Remap" : "Map this camera"}
                    </button>
                    {mapping ? (
                      <span className={styles.help} style={{ alignSelf: "center" }}>
                        Last mapped {new Date(mapping.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {mapError ? <div className={styles.error} role="alert">{mapError}</div> : null}

      <div className={styles.privacyLine}>
        <span aria-hidden="true">◇</span>
        <p>
          <strong>The frame does not leave this machine.</strong> The setup scan is a local
          YOLO pass. The browser sends the JPEG to the local backend, which returns labels
          and boxes and keeps neither. Muse is not called by the scan.
        </p>
      </div>

      <div className={styles.sectionCard}>
        <div className={styles.sectionHead}>
          <div>
            <h2 className={styles.sectionTitle}>Muse configures the plan</h2>
            <p className={styles.sectionNote}>
              Once every camera is mapped, Muse turns the object labels and your answers into
              a proposed watch plan. You approve it before it runs.
            </p>
          </div>
          <span className={styles.count}>{setup.proposal ? "PROPOSED" : allMapped ? "READY" : "WAITING"}</span>
        </div>

        <label className={styles.consentLine}>
          <input
            type="checkbox"
            checked={setup.museConsent}
            onChange={(event) => setMuseConsent(event.target.checked)}
          />
          <span>
            Allow Muse to tune the plan from these answers and the camera object labels.
            It receives no images, no audio, and no face data.
          </span>
        </label>

        {!allMapped ? (
          <p className={styles.museText}>
            {missingMap.length === 1
              ? `${missingMap[0].name || missingMap[0].label} still needs a map.`
              : `${missingMap.length} cameras still need a map.`} Muse will configure after that.
          </p>
        ) : null}

        {proposing ? (
          <div className={styles.loading} style={{ marginTop: 14 }}>
            <span className={styles.spinner} />
            Muse is reading the mappings and preparing a plan…
          </div>
        ) : null}

        {museError ? (
          <div className={styles.error} role="alert">
            {museError}
            <button
              type="button"
              className={styles.quiet}
              style={{ marginLeft: 10, minHeight: 0, padding: 0 }}
              onClick={() => void propose(true)}
            >
              Retry
            </button>
          </div>
        ) : null}

        {setup.proposal ? (
          <div className={styles.museBox}>
            <div className={styles.museHead}>
              <div>
                <p className={styles.museTitle}>
                  {setup.configApproved && museApplied ? "Muse's configuration is approved" : "Muse prepared a configuration"}
                </p>
                <p className={styles.museText}>{setup.proposal.summary}</p>
              </div>
              <span className={styles.count}>{setup.proposal.model.replace("meta.", "")}</span>
            </div>
            {setup.proposal.notes.length ? (
              <ul className={styles.museNotes}>
                {setup.proposal.notes.map((note) => <li key={note}>{note}</li>)}
              </ul>
            ) : null}
            <div className={styles.mapActions}>
              <button type="button" className={styles.primary} onClick={() => setShowProposal(true)}>
                Review proposal
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={askMuseAgain}
                disabled={proposing || !setup.museConsent}
              >
                Ask Muse again
              </button>
            </div>
            {!setup.configApproved ? (
              <p className={styles.modalNote}>
                The configuration has not been applied. Approve it here, or approve your own
                plan on the final step.
              </p>
            ) : null}
          </div>
        ) : allMapped && setup.museConsent && !proposing && !museError ? (
          <div className={styles.mapActions}>
            <button type="button" className={styles.secondary} onClick={() => void propose(true)}>
              Configure with Muse
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
