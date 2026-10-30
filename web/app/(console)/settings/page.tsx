"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { FeedManager } from "@/components/feed-manager";
import { useConsole } from "@/components/console-provider";
import { hourLabel, send, type WatchClass, type WatchState } from "@/lib/api";
import {
  conditionLabel,
  setupReadyToWatch,
  type ConsoleFeed,
  type ConsoleSetup,
} from "@/lib/console";
import { useApi } from "@/lib/useApi";
import shared from "@/components/console-shared.module.css";
import styles from "@/components/console-pages.module.css";

const MODE_LABEL: Record<string, string> = {
  alert: "Alert",
  digest: "Digest",
  off: "Quiet",
};

export default function SettingsPage() {
  const { setup, updateSetup, resident, active, setActive } = useConsole();
  const watch = useApi<WatchState>("/api/watch", 0);
  const [initialized, setInitialized] = useState(false);
  const [modes, setModes] = useState<Record<string, string>>({});
  const [quiet, setQuiet] = useState({ enabled: true, start: 22, end: 7 });
  const [doorHours, setDoorHours] = useState<"night" | "always">("night");
  const [caregiver, setCaregiver] = useState("");
  const [caregiverPhone, setCaregiverPhone] = useState("");
  const [savingPlan, setSavingPlan] = useState(false);
  const [planSaved, setPlanSaved] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [savedContact, setSavedContact] = useState(false);

  useEffect(() => {
    if (!setup || initialized) return;
    const source = Object.keys(setup.watchModes).length
      ? { modes: setup.watchModes, quiet: setup.quiet, door: setup.doorHours }
      : watch.data
        ? { modes: watch.data.plan.modes, quiet: watch.data.plan.quiet, door: watch.data.plan.door_hours }
        : null;
    if (!source) return;
    setModes(source.modes);
    setQuiet(source.quiet);
    setDoorHours(source.door);
    setCaregiver(setup.caregiver);
    setCaregiverPhone(setup.caregiverPhone);
    setInitialized(true);
  }, [setup, initialized, watch.data]);

  const readiness = useMemo(() => setupReadyToWatch(setup), [setup]);
  const classes = watch.data?.classes ?? [];
  const mapped = setup ? setup.feeds.filter((feed) => setup.mapping[feed.id]).length : 0;

  const mutateFeeds = (feeds: ConsoleFeed[]) => {
    updateSetup((current: ConsoleSetup) => {
      const before = current.feeds.map((feed) => feed.id).sort().join("|");
      const after = feeds.map((feed) => feed.id).sort().join("|");
      if (before === after) return { ...current, feeds };
      const valid = new Set(feeds.map((feed) => feed.id));
      return {
        ...current,
        feeds,
        mapping: Object.fromEntries(
          Object.entries(current.mapping).filter(([id]) => valid.has(id)),
        ),
        proposal: null,
        configApproved: false,
        configApprovedAt: null,
      };
    });
  };

  const savePlan = async () => {
    setSavingPlan(true);
    setPlanError(null);
    setPlanSaved(null);
    try {
      await send("/api/watch", { modes, door_hours: doorHours, quiet, complete: true }, "PUT");
      updateSetup((current) => ({
        ...current,
        watchModes: { ...modes },
        quiet: { ...quiet },
        doorHours,
        configApproved: true,
        configApprovedAt: new Date().toISOString(),
      }));
      setPlanSaved("Saved to the household plan");
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : "The house did not accept the new plan.");
    } finally {
      setSavingPlan(false);
    }
  };

  const saveContact = () => {
    updateSetup((current) => ({ ...current, caregiver, caregiverPhone }));
    setSavedContact(true);
    window.setTimeout(() => setSavedContact(false), 2500);
  };

  if (!setup) return null;

  const setupHref = !setup.feeds.length
    ? "/setup?step=cameras"
    : setup.feeds.some((feed) => !setup.mapping[feed.id])
      ? "/setup?step=mapping"
      : !setup.configApproved
        ? "/setup?step=finish"
        : "/setup";

  return (
    <div className={shared.page}>
      <div className={shared.pageHead}>
        <div>
          <p className={shared.kicker}>Installation</p>
          <h1 className={shared.pageTitle}>Settings that change what can reach you.</h1>
          <p className={shared.pageLede}>
            Cameras, the watch plan, and the notice that makes the system legible. Safety classes
            stay locked; everything else here changes the volume, not the net.
          </p>
        </div>
        <div className={shared.headStat}>
          <strong>{mapped}/{setup.feeds.length}</strong>
          <span>views mapped</span>
        </div>
      </div>

      {!readiness.ready ? (
        <div className={shared.noteBar}>
          <span>{readiness.reason}</span>
          <Link href={setupHref}>Finish setup</Link>
        </div>
      ) : (
        <div className={shared.noteBar} data-tone="sage">
          <span>
            {active
              ? "The system is watching. Changes here apply to the next analyzed frame."
              : "The system is in standby. Settings are saved, but no camera is open."}
          </span>
          <button type="button" className={shared.ghostButton} onClick={() => void setActive(!active)}>
            {active ? "Pause system" : "Activate system"}
          </button>
        </div>
      )}

      <div className={styles.settingsGrid}>
        <div className={styles.settingsCol}>
          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <div>
                <h2 className={styles.panelTitle}>Cameras</h2>
                <p className={styles.panelNote}>
                  Add a local recording, a device camera, or lab footage. Renaming a view does not
                  invalidate its map; adding a new one does, because a new room has never been seen.
                </p>
              </div>
              <span className={styles.panelCode}>{setup.feeds.length} VIEWS</span>
            </div>
            <div className={styles.panelBody}>
              <FeedManager tone="dark" feeds={setup.feeds} onChange={mutateFeeds} />
              <div className={styles.mappingList}>
                {setup.feeds.map((feed) => {
                  const mapping = setup.mapping[feed.id];
                  return (
                    <div className={styles.mappingRow} key={feed.id}>
                      <div>
                        <div className={styles.mappingRowName}>{feed.name || feed.label}</div>
                        <div className={styles.mappingRowMeta}>
                          {feed.location || "No room label"} · {feed.kind}
                          {mapping ? ` · ${mapping.personCount} ${mapping.personCount === 1 ? "person" : "people"} · ${mapping.objects.length} ${mapping.objects.length === 1 ? "object" : "objects"}` : ""}
                        </div>
                      </div>
                      <span className={styles.mappingRowStatus} data-ok={Boolean(mapping)}>
                        <span className={styles.dot} />
                        {mapping ? "Mapped" : "Unmapped"}
                      </span>
                    </div>
                  );
                })}
              </div>
              <div className={styles.setupLinkRow}>
                <Link className={styles.setupLink} href="/setup?step=cameras">Manage camera inputs</Link>
                <Link className={styles.setupLink} href="/setup?step=mapping">Map views &amp; configure with Muse</Link>
              </div>
            </div>
          </section>

          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <div>
                <h2 className={styles.panelTitle}>Watch plan</h2>
                <p className={styles.panelNote}>
                  The plan is stored in the backend and read by the policy floor on every event.
                  Locked classes are the net, not volume.
                </p>
              </div>
              <span className={styles.panelCode}>{classes.length} CLASSES</span>
            </div>
            <div className={styles.panelBody}>
              {watch.error ? <p className={styles.errorNote} role="alert">{watch.error}</p> : null}
              <div className={styles.modeSettings}>
                {classes.map((spec) => (
                  <SettingMode
                    key={spec.id}
                    spec={spec}
                    mode={modes[spec.id] ?? spec.default}
                    onMode={(mode) => {
                      setModes((current) => ({ ...current, [spec.id]: mode }));
                      setPlanSaved(null);
                    }}
                  />
                ))}
              </div>

              <div className={styles.quietGrid}>
                <div className={styles.settingsField}>
                  <span>Quiet hours</span>
                  <label className={styles.checkRow} style={{ color: "var(--c-muted)" }}>
                    <input type="checkbox" checked={quiet.enabled}
                      onChange={(event) => setQuiet({ ...quiet, enabled: event.target.checked })} />
                    Enabled
                  </label>
                </div>
                <div className={styles.settingsField}>
                  <span>Door alerts</span>
                  <select value={doorHours} onChange={(event) => setDoorHours(event.target.value as "night" | "always")}>
                    <option value="night">Only after dark</option>
                    <option value="always">Any time</option>
                  </select>
                </div>
                {quiet.enabled ? (
                  <>
                    <div className={styles.settingsField}>
                      <span>Quiet from</span>
                      <select value={quiet.start} onChange={(event) => setQuiet({ ...quiet, start: Number(event.target.value) })}>
                        {Array.from({ length: 24 }, (_, hour) => (
                          <option key={hour} value={hour}>{hourLabel(hour)}</option>
                        ))}
                      </select>
                    </div>
                    <div className={styles.settingsField}>
                      <span>Quiet until</span>
                      <select value={quiet.end} onChange={(event) => setQuiet({ ...quiet, end: Number(event.target.value) })}>
                        {Array.from({ length: 24 }, (_, hour) => (
                          <option key={hour} value={hour}>{hourLabel(hour)}</option>
                        ))}
                      </select>
                    </div>
                  </>
                ) : null}
              </div>

              <div className={styles.saveRow}>
                <button type="button" className={shared.primaryButton} onClick={() => void savePlan()}
                  disabled={savingPlan || !classes.length}>
                  {savingPlan ? "Saving…" : "Save watch plan"}
                </button>
                {planSaved ? <span className={styles.savedNote}>{planSaved}</span> : null}
              </div>
              {planError ? <p className={styles.errorNote} role="alert">{planError}</p> : null}
            </div>
          </section>
        </div>

        <div className={styles.settingsCol}>
          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <div>
                <h2 className={styles.panelTitle}>The household</h2>
                <p className={styles.panelNote}>
                  Names and conditions live in setup so the alert copy and the policy floor read one
                  source of truth.
                </p>
              </div>
            </div>
            <div className={styles.panelBody}>
              {setup.occupants.filter((person) => person.name.trim()).map((person, index) => (
                <div key={person.id} className={styles.mappingRow} style={{ marginBottom: 6 }}>
                  <div>
                    <div className={styles.mappingRowName}>
                      {person.name.trim()}{index === 0 ? " · primary" : ""}
                    </div>
                    <div className={styles.mappingRowMeta}>
                      {person.age ? `${person.age} · ` : ""}{person.relationship || "household"}
                      {person.conditions.length ? ` · ${person.conditions.map(conditionLabel).join(", ")}` : ""}
                    </div>
                  </div>
                </div>
              ))}
              <div className={styles.setupLinkRow}>
                <Link className={styles.setupLink} href="/setup?step=people">Edit people &amp; conditions</Link>
              </div>
            </div>
          </section>

          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <div>
                <h2 className={styles.panelTitle}>Family contact</h2>
                <p className={styles.panelNote}>
                  Kept on this device for the console&apos;s own labels. Push delivery uses the
                  subscriptions in the privacy settings, not this field.
                </p>
              </div>
            </div>
            <div className={styles.panelBody}>
              <label className={styles.settingsField}>
                <span>Name</span>
                <input value={caregiver} onChange={(event) => { setCaregiver(event.target.value); setSavedContact(false); }}
                  placeholder={resident?.name ? `${resident.name}'s family` : "Family contact"} />
              </label>
              <label className={styles.settingsField}>
                <span>Phone</span>
                <input value={caregiverPhone} onChange={(event) => { setCaregiverPhone(event.target.value); setSavedContact(false); }}
                  placeholder="+20 …" inputMode="tel" />
              </label>
              <div className={styles.saveRow}>
                <button type="button" className={shared.smallButton} onClick={saveContact}>Save contact</button>
                {savedContact ? <span className={styles.savedNote}>Saved on this device</span> : null}
              </div>
            </div>
          </section>

          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <div>
                <h2 className={styles.panelTitle}>Privacy &amp; retention</h2>
                <p className={styles.panelNote}>
                  Mapping frames never leave this machine. Muse receives structured labels and the
                  answers you gave, not video.
                </p>
              </div>
            </div>
            <div className={styles.panelBody}>
              <div className={shared.focusFacts} style={{ border: "1px solid var(--c-line-soft)" }}>
                <div className={shared.focusFact}><span>Local clips</span><strong>7 days</strong></div>
                <div className={shared.focusFact}><span>Upstream</span><strong>1 hour</strong></div>
                <div className={shared.focusFact}><span>Face recognition</span><strong>off</strong></div>
                <div className={shared.focusFact}><span>Idle frames</span><strong>never uploaded</strong></div>
              </div>
              <div className={styles.setupLinkRow}>
                <Link className={styles.setupLink} href="/privacy">Open privacy controls</Link>
                <Link className={styles.setupLink} href="/privacy#delete">Delete recorded media</Link>
              </div>
            </div>
          </section>

          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <div>
                <h2 className={styles.panelTitle}>Installation record</h2>
                <p className={styles.panelNote}>
                  The console blocks activation until every view is mapped and the plan is approved.
                </p>
              </div>
            </div>
            <div className={styles.panelBody}>
              <div className={styles.mappingRow}>
                <div>
                  <div className={styles.mappingRowName}>Configuration</div>
                  <div className={styles.mappingRowMeta}>
                    {setup.configApproved
                      ? `Approved${setup.configApprovedAt ? ` · ${new Date(setup.configApprovedAt).toLocaleDateString("en-GB")}` : ""}`
                      : "Not approved"}
                  </div>
                </div>
                <span className={styles.mappingRowStatus} data-ok={setup.configApproved}>
                  <span className={styles.dot} />
                  {setup.configApproved ? "Approved" : "Required"}
                </span>
              </div>
              <div className={styles.setupLinkRow}>
                <Link className={styles.setupLink} href={setupHref}>
                  {readiness.ready ? "Review installation" : "Finish installation"}
                </Link>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function SettingMode({
  spec,
  mode,
  onMode,
}: {
  spec: WatchClass;
  mode: string;
  onMode: (mode: string) => void;
}) {
  const options = spec.modes?.length ? spec.modes : ["alert", "digest", "off"];
  return (
    <div className={styles.modeSetting}>
      <div>
        <div className={styles.modeSettingName}>
          {spec.label}
          {spec.locked ? <span style={{ color: "var(--c-red)", marginLeft: 7, fontFamily: "var(--font-mono)", fontSize: 7.5 }}>LOCKED</span> : null}
        </div>
        <p className={styles.modeSettingHint}>{spec.question}</p>
      </div>
      <div className={styles.modeToggle} role="radiogroup" aria-label={spec.label}>
        {options.map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={mode === option}
            data-on={mode === option}
            disabled={spec.locked}
            title={spec.examples?.[option]}
            onClick={() => onMode(option)}
          >
            {MODE_LABEL[option] ?? option}
          </button>
        ))}
      </div>
    </div>
  );
}
