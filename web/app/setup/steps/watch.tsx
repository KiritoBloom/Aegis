import { hourLabel } from "@/lib/api";
import { MODE_SHORT, type SetupController } from "../controller";
import type { WatchClass } from "@/lib/api";
import styles from "../setup.module.css";

export function WatchStep({ c }: { c: SetupController }) {
  const { setup, watch, classes, counts, rejectedMuse, mutate, setMode } = c;
  return (
    <section>
      <h1 className={styles.title}>What deserves your attention?</h1>
      <p className={styles.lede}>
        Safety classes are locked. Everything else is a volume knob: interrupt now, wait for
        the morning note, or keep it entirely quiet. The count still happens either way.
      </p>

      {rejectedMuse ? (
        <div className={styles.error} role="status">
          Muse&apos;s proposal was discarded. Your choices below are the plan. Review them and
          approve your own configuration at the end.
        </div>
      ) : null}

      {watch.error ? (
        <div className={styles.error} role="alert">
          The watch plan could not be read from the house: {watch.error}
          <button type="button" className={styles.quiet} onClick={watch.reload} style={{ marginLeft: 10 }}>
            Try again
          </button>
        </div>
      ) : null}

      <div className={styles.sectionCard}>
        <div className={styles.sectionHead}>
          <div>
            <h2 className={styles.sectionTitle}>The plan</h2>
            <p className={styles.sectionNote}>
              {counts.locked} safety classes always reach you. {counts.alert} optional classes
              interrupt. {counts.held} wait or stay quiet.
            </p>
          </div>
          <span className={styles.count}>{classes.length} classes</span>
        </div>

        <div className={styles.modeList}>
          {classes.map((spec) => (
            <ModeRow
              key={spec.id}
              spec={spec}
              mode={setup.watchModes[spec.id] ?? spec.default}
              onMode={(mode) => setMode(spec.id, mode)}
            />
          ))}
          {!classes.length && !watch.loading ? (
            <p className={styles.help}>No watch classes were returned by this backend.</p>
          ) : null}
        </div>
      </div>

      <div className={styles.sectionCard}>
        <div className={styles.sectionHead}>
          <div>
            <h2 className={styles.sectionTitle}>Quiet hours</h2>
            <p className={styles.sectionNote}>
              Inside this window, a &ldquo;worth a look&rdquo; waits for the morning note. A fall, a
              no-answer, or a blind camera never waits.
            </p>
          </div>
          <span className={styles.count}>
            {setup.quiet.enabled
              ? `${hourLabel(setup.quiet.start)} – ${hourLabel(setup.quiet.end)}`
              : "OFF"}
          </span>
        </div>
        <label className={styles.checkRow} style={{ marginTop: 16 }}>
          <input
            type="checkbox"
            checked={setup.quiet.enabled}
            onChange={(event) => mutate((current) => ({
              ...current,
              quiet: { ...current.quiet, enabled: event.target.checked },
            }))}
          />
          Use quiet hours
        </label>
        {setup.quiet.enabled ? (
          <div className={styles.hourRow}>
            <span>From</span>
            <select
              className={styles.hourInput}
              value={setup.quiet.start}
              onChange={(event) => mutate((current) => ({
                ...current,
                quiet: { ...current.quiet, start: Number(event.target.value) },
              }))}
            >
              {Array.from({ length: 24 }, (_, hour) => (
                <option key={hour} value={hour}>{hourLabel(hour)}</option>
              ))}
            </select>
            <span>until</span>
            <select
              className={styles.hourInput}
              value={setup.quiet.end}
              onChange={(event) => mutate((current) => ({
                ...current,
                quiet: { ...current.quiet, end: Number(event.target.value) },
              }))}
            >
              {Array.from({ length: 24 }, (_, hour) => (
                <option key={hour} value={hour}>{hourLabel(hour)}</option>
              ))}
            </select>
            <span className={styles.help}>A morning note arrives after the window.</span>
          </div>
        ) : null}
      </div>

      <div className={styles.sectionCard}>
        <div className={styles.sectionHead}>
          <div>
            <h2 className={styles.sectionTitle}>The front door</h2>
            <p className={styles.sectionNote}>
              Separate from quiet hours, because a daytime delivery and a 3 AM exit are not
              the same event.
            </p>
          </div>
        </div>
        <div className={styles.choiceGrid}>
          {([
            ["night", "Only after dark", "Daytime openings wait for the morning note."],
            ["always", "Any time", "Every opening can reach a phone."],
          ] as const).map(([value, label, hint]) => (
            <button
              key={value}
              type="button"
              className={styles.choice}
              data-on={setup.doorHours === value}
              onClick={() => mutate((current) => ({ ...current, doorHours: value }))}
            >
              <strong>{label}</strong>
              <span>{hint}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function ModeRow({ spec, mode, onMode }: { spec: WatchClass; mode: string; onMode: (mode: string) => void }) {
  const options = spec.modes?.length ? spec.modes : ["alert", "digest", "off"];
  return (
    <div className={styles.modeRow}>
      <div>
        <p className={styles.modeName}>
          {spec.label}
          {spec.locked ? <span className={styles.lockedChip}>locked</span> : null}
        </p>
        <p className={styles.modeQuestion}>{spec.question}</p>
      </div>
      <div className={styles.modeButtons} role="radiogroup" aria-label={spec.label}>
        {options.map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={mode === option}
            className={styles.modeButton}
            data-on={mode === option}
            disabled={spec.locked}
            title={spec.examples?.[option] ?? option}
            onClick={() => onMode(option)}
          >
            {MODE_SHORT[option] ?? option}
          </button>
        ))}
      </div>
    </div>
  );
}
