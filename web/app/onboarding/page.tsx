"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/ui";
import {
  hourLabel,
  send,
  type Household,
  type WatchClass,
  type WatchPlan,
  type WatchState,
  type Zone,
} from "@/lib/api";
import { useApi } from "@/lib/useApi";

/* ---------------------------------------------------------------------------
   Onboarding.

   The rule for this whole flow: one idea per screen, every screen already
   answered. A worried daughter setting this up at 11 PM should be able to press
   Continue six times and end up with something correct. Nothing here is a
   required form field in the usual sense — the defaults are the product's
   opinion, and the job of the flow is to let her disagree with it.

   The last screen is not a "you're all set". It is a contract: here is exactly
   what will interrupt you, here is what will not, and here is the one thing you
   cannot turn off. Then the resident consents, by name, on the record.
--------------------------------------------------------------------------- */

type StepId = "welcome" | "resident" | "areas" | "alerts" | "quiet" | "consent";

const STEPS: { id: StepId; label: string }[] = [
  { id: "welcome", label: "Start" },
  { id: "resident", label: "Who" },
  { id: "areas", label: "Where" },
  { id: "alerts", label: "What" },
  { id: "quiet", label: "When" },
  { id: "consent", label: "Agree" },
];

const MODE_COPY: Record<string, { label: string; hint: string }> = {
  alert: { label: "Tell me now", hint: "A push, day or night." },
  digest: { label: "Morning letter", hint: "Recorded, not a buzz." },
  off: { label: "Keep it quiet", hint: "Counted, never shown." },
};

export default function Onboarding() {
  const router = useRouter();
  const watch = useApi<WatchState>("/api/watch", 0);
  const household = useApi<Household>("/api/household", 0);

  const [step, setStep] = useState<StepId>("welcome");
  const [plan, setPlan] = useState<WatchPlan | null>(null);
  const [profile, setProfile] = useState<Household["profile"] | null>(null);
  const [armed, setArmed] = useState<Record<string, boolean>>({});
  const [consentName, setConsentName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (watch.data && !plan) setPlan(watch.data.plan);
  }, [watch.data, plan]);

  useEffect(() => {
    if (household.data && !profile) {
      setProfile(household.data.profile);
      setConsentName(household.data.profile.display_name);
    }
  }, [household.data, profile]);

  useEffect(() => {
    if (watch.data && Object.keys(armed).length === 0) {
      // An existing plan's answers win over the default of "everything on", so
      // reopening setup does not silently re-arm an area somebody switched off.
      const saved = watch.data.plan.zones ?? {};
      setArmed(
        Object.fromEntries(
          watch.data.zones.map((z) => [z.name, z.private ? false : saved[z.name] !== false]),
        ),
      );
    }
  }, [watch.data, armed]);

  const classes = watch.data?.classes ?? [];
  const zones = watch.data?.zones ?? [];
  const index = STEPS.findIndex((s) => s.id === step);

  // Areas a person can switch off. Private zones are masked before any frame
  // leaves the camera, so they are not a preference and never appear here.
  const switchable = zones.filter((z) => !z.private);
  const unwatched = switchable.filter((z) => armed[z.name] === false).map((z) => z.name);

  const counts = useMemo(() => {
    if (!plan) return { alert: 0, held: 0, locked: 0 };
    let alert = 0;
    let held = 0;
    let locked = 0;
    for (const c of classes) {
      const mode = plan.modes[c.id] ?? c.default;
      if (c.locked) locked += 1;
      else if (mode === "alert") alert += 1;
      else held += 1;
    }
    return { alert, held, locked };
  }, [plan, classes]);

  const setMode = (id: string, mode: string) =>
    setPlan((p) => (p ? { ...p, modes: { ...p.modes, [id]: mode } } : p));

  const go = (delta: number) => {
    const next = STEPS[index + delta];
    if (next) {
      setStep(next.id);
      window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
    }
  };

  const finish = async () => {
    if (!plan) return;
    setSaving(true);
    setError(null);
    try {
      if (profile) {
        await send("/api/household", { profile }, "PUT");
      }
      const planWithConsent: WatchPlan = {
        ...plan,
        // The areas screen asked a real question; carry the answer into the
        // plan or the toggles are decoration.
        zones: armed,
        consent_at: new Date().toISOString(),
        consent_by: consentName.trim() || profile?.display_name || "resident",
      };
      await send("/api/watch", { ...planWithConsent, complete: true }, "PUT");
      router.push("/?welcome=1");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the plan");
      setSaving(false);
    }
  };

  if (watch.error) {
    return (
      <Shell>
        <p className="h1" style={{ margin: 0 }}>The house is not answering.</p>
        <p className="lede" style={{ marginTop: 12 }}>
          Setup needs a connection to the machine running Aegis. Nothing has been
          changed, and no default has been silently accepted on your behalf.
        </p>
        <p className="mono micro" style={{ marginTop: 16 }}>{watch.error}</p>
        <button className="btn" style={{ marginTop: 20 }} onClick={watch.reload}>
          Try again
        </button>
      </Shell>
    );
  }

  if (!plan || !profile || watch.loading) {
    return (
      <Shell>
        <div style={{ display: "grid", gap: 14, maxWidth: 560 }}>
          <div className="skel" style={{ height: 13, width: 120 }} />
          <div className="skel" style={{ height: 40, width: "80%" }} />
          <div className="skel" style={{ height: 18, width: "100%" }} />
          <div className="skel" style={{ height: 18, width: "70%" }} />
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      {/* progress: a hairline, not a wizard bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 34 }}>
        <span className="mono micro" style={{ letterSpacing: "0.08em" }}>
          {String(index + 1).padStart(2, "0")} / {String(STEPS.length).padStart(2, "0")}
        </span>
        <div style={{ flex: 1, height: 1, background: "var(--color-line)" }}>
          <div
            style={{
              height: 1,
              width: `${((index + 1) / STEPS.length) * 100}%`,
              background: "var(--color-ink)",
              transition: "width 240ms ease-out",
            }}
          />
        </div>
        <button
          className="btn btn-quiet mono"
          style={{ minHeight: 32, fontSize: 12.5 }}
          onClick={() => router.push("/")}
        >
          Leave setup
        </button>
      </div>

      {step === "welcome" ? (
        <section>
          <p className="eyebrow">Aegis · setup</p>
          <h1 className="h1" style={{ margin: "10px 0 0", maxWidth: 640 }}>
            Let&apos;s decide what deserves your attention.
          </h1>
          <p className="lede" style={{ marginTop: 16, maxWidth: 560 }}>
            Aegis watches one camera in Margaret&apos;s home. Most of what it sees
            is none of your business — a kettle, a cat, a nap. This takes about
            two minutes and decides the small number of things that interrupt you.
          </p>

          <div style={{ display: "grid", gap: 10, marginTop: 30, maxWidth: 560 }}>
            {[
              ["It watches, it does not record.", "Frames stay in the camera's memory for seconds. A clip is kept only when something happens."],
              ["You choose the volume.", "Door opens, the stove, long stillness. Each one can be a push, a morning line, or silent."],
              ["Three things you cannot switch off.", "A fall, no answer to a check-in, and the camera going blind. Everything else is yours to decide."],
            ].map(([title, body]) => (
              <div
                key={title}
                className="card-flat"
                style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: "18px 1fr", gap: 12 }}
              >
                <span style={{ color: "var(--color-sage)", marginTop: 2 }}>
                  <Icon name="check" size={17} />
                </span>
                <div>
                  <p className="h3" style={{ margin: 0 }}>{title}</p>
                  <p className="small" style={{ margin: "3px 0 0" }}>{body}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {step === "resident" ? (
        <section>
          <p className="eyebrow">Who this is for</p>
          <h1 className="h1" style={{ margin: "10px 0 0", maxWidth: 600 }}>
            These answers change how quickly we call you.
          </h1>
          <p className="lede" style={{ marginTop: 14, maxWidth: 560 }}>
            Not paperwork. A fall for an 81-year-old who lives alone and takes a
            blood thinner is a different event from the same fall for someone
            younger and stronger, and the alert says so.
          </p>

          <div style={{ display: "grid", gap: 16, marginTop: 28, maxWidth: 560 }}>
            <label style={{ display: "grid", gap: 6 }}>
              <span className="micro">Her name</span>
              <input
                className="input"
                value={profile.display_name}
                onChange={(e) => setProfile({ ...profile, display_name: e.target.value })}
                autoComplete="off"
              />
            </label>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label style={{ display: "grid", gap: 6 }}>
                <span className="micro">Age</span>
                <input
                  className="input mono"
                  type="number"
                  min={1}
                  max={129}
                  value={profile.age}
                  onChange={(e) => setProfile({ ...profile, age: Number(e.target.value) })}
                />
              </label>
              <label style={{ display: "grid", gap: 6 }}>
                <span className="micro">Memory</span>
                <select
                  className="input"
                  value={profile.cognitive_condition}
                  onChange={(e) => setProfile({ ...profile, cognitive_condition: e.target.value })}
                >
                  <option value="dementia">Dementia</option>
                  <option value="mild_cognitive_impairment">Mild impairment</option>
                  <option value="none">No diagnosis</option>
                </select>
              </label>
            </div>

            <div style={{ display: "grid", gap: 8 }}>
              {(
                [
                  ["lives_alone", "She lives alone", "No one else is in the house to notice."],
                  ["wanders", "She has wandered before", "The front door becomes the highest-risk object in the home."],
                  ["anticoagulant", "She takes a blood thinner", "A fall that looks minor can bleed internally."],
                  ["uses_mobility_aid", "She uses a stick or frame", "Changes what a slow crossing of a room means."],
                ] as const
              ).map(([key, label, hint]) => (
                <button
                  key={key}
                  type="button"
                  className="choice"
                  aria-checked={Boolean(profile[key])}
                  onClick={() => setProfile({ ...profile, [key]: !profile[key] })}
                >
                  <span className="radio" />
                  <span>
                    <span className="h3" style={{ display: "block" }}>{label}</span>
                    <span className="micro" style={{ display: "block", marginTop: 2 }}>{hint}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {step === "areas" ? (
        <section>
          <p className="eyebrow">What the camera can see</p>
          <h1 className="h1" style={{ margin: "10px 0 0", maxWidth: 620 }}>
            Four areas. One of them is not watched, ever.
          </h1>
          <p className="lede" style={{ marginTop: 14, maxWidth: 570 }}>
            Aegis found these in the camera&apos;s view. Turn an area off and the
            rules that depend on it stop — the hallway going quiet means falls in
            the hallway stop being caught, so the trade is spelled out rather
            than buried.
          </p>

          <div style={{ display: "grid", gap: 10, marginTop: 26, maxWidth: 620 }}>
            {zones.map((z) => (
              <AreaRow
                key={z.name}
                zone={z}
                on={Boolean(armed[z.name])}
                onToggle={() =>
                  !z.private && setArmed((a) => ({ ...a, [z.name]: !a[z.name] }))
                }
              />
            ))}
          </div>

          <p className="micro" style={{ marginTop: 18, maxWidth: 560 }}>
            The bathroom is masked before any frame leaves the camera. There is no
            setting that unmutes it — that is a hardware-and-software promise, not
            a preference.
          </p>
        </section>
      ) : null}

      {step === "alerts" ? (
        <section>
          <p className="eyebrow">What should reach you</p>
          <h1 className="h1" style={{ margin: "10px 0 0", maxWidth: 640 }}>
            Every one of these is a real thing the camera can notice.
          </h1>
          <p className="lede" style={{ marginTop: 14, maxWidth: 570 }}>
            Three of them cannot be turned off, and they are marked. The rest are
            a volume knob: a push now, a line in the morning letter, or nothing at
            all — with the count still kept, so you can see what you chose to miss.
          </p>

          <div style={{ display: "grid", gap: 12, marginTop: 26, maxWidth: 660 }}>
            {classes.map((c) => (
              <ClassCard
                key={c.id}
                spec={c}
                mode={plan.modes[c.id] ?? c.default}
                onMode={(m) => setMode(c.id, m)}
              />
            ))}
          </div>
        </section>
      ) : null}

      {step === "quiet" ? (
        <section>
          <p className="eyebrow">When to stay quiet</p>
          <h1 className="h1" style={{ margin: "10px 0 0", maxWidth: 600 }}>
            Night is a different house.
          </h1>
          <p className="lede" style={{ marginTop: 14, maxWidth: 560 }}>
            Inside these hours, a &ldquo;worth a look&rdquo; waits for the morning.
            An emergency never does — nothing on this screen can silence a fall.
          </p>

          <div className="card" style={{ padding: 20, marginTop: 26, maxWidth: 560 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
              <div>
                <p className="h3" style={{ margin: 0 }}>Quiet hours</p>
                <p className="micro" style={{ margin: "3px 0 0" }}>
                  {plan.quiet.enabled
                    ? `${hourLabel(plan.quiet.start)} to ${hourLabel(plan.quiet.end)}`
                    : "Off — everything that is worth a look arrives immediately"}
                </p>
              </div>
              <Toggle
                on={plan.quiet.enabled}
                onChange={(v) => setPlan({ ...plan, quiet: { ...plan.quiet, enabled: v } })}
                label="Quiet hours"
              />
            </div>

            {plan.quiet.enabled ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 18 }}>
                <label style={{ display: "grid", gap: 6 }}>
                  <span className="micro">From</span>
                  <select
                    className="input mono"
                    value={plan.quiet.start}
                    onChange={(e) =>
                      setPlan({ ...plan, quiet: { ...plan.quiet, start: Number(e.target.value) } })
                    }
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>{hourLabel(h)}</option>
                    ))}
                  </select>
                </label>
                <label style={{ display: "grid", gap: 6 }}>
                  <span className="micro">Until</span>
                  <select
                    className="input mono"
                    value={plan.quiet.end}
                    onChange={(e) =>
                      setPlan({ ...plan, quiet: { ...plan.quiet, end: Number(e.target.value) } })
                    }
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>{hourLabel(h)}</option>
                    ))}
                  </select>
                </label>
              </div>
            ) : null}
          </div>

          <div className="card" style={{ padding: 20, marginTop: 14, maxWidth: 560 }}>
            <p className="h3" style={{ margin: 0 }}>When the front door opens</p>
            <p className="micro" style={{ margin: "3px 0 14px" }}>
              This is separate from quiet hours, because it is about the door rather
              than the clock in your head.
            </p>
            <div style={{ display: "grid", gap: 8 }}>
              {(
                [
                  ["night", "Only at night", "Daytime opens are a delivery; night opens are the risk."],
                  ["always", "Any time", "Every crossing of the door reaches you."],
                ] as const
              ).map(([value, label, hint]) => (
                <button
                  key={value}
                  type="button"
                  className="choice"
                  aria-checked={plan.door_hours === value}
                  onClick={() => setPlan({ ...plan, door_hours: value })}
                >
                  <span className="radio" />
                  <span>
                    <span className="h3" style={{ display: "block" }}>{label}</span>
                    <span className="micro" style={{ display: "block", marginTop: 2 }}>{hint}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {step === "consent" ? (
        <section>
          <p className="eyebrow">The agreement</p>
          <h1 className="h1" style={{ margin: "10px 0 0", maxWidth: 640 }}>
            Here is exactly what you are both agreeing to.
          </h1>

          <div className="card" style={{ padding: 22, marginTop: 26, maxWidth: 660 }}>
            <div style={{ display: "grid", gap: 14 }}>
              <ContractLine
                tone="red"
                title={`${counts.locked} things will always reach you`}
                body="A fall, a check-in that got no answer, and the camera going blind. These are the reason the system exists; they are not settings."
              />
              <ContractLine
                tone="sage"
                title={`${counts.alert} thing${counts.alert === 1 ? "" : "s"} will interrupt you`}
                body={
                  counts.alert
                    ? classes
                        .filter((c) => !c.locked && plan.modes[c.id] === "alert")
                        .map((c) => c.label.toLowerCase())
                        .join(", ") + "."
                    : "Nothing beyond the three above. The house will be very quiet."
                }
              />
              <ContractLine
                tone="amber"
                title={`${counts.held} will wait or stay silent`}
                body={
                  counts.held
                    ? classes
                        .filter((c) => !c.locked && plan.modes[c.id] !== "alert")
                        .map((c) => `${c.label.toLowerCase()} (${MODE_COPY[plan.modes[c.id]]?.label.toLowerCase()})`)
                        .join(", ") + ". You will still see the count on the front page."
                    : "Nothing is being held back."
                }
              />
              <ContractLine
                tone={unwatched.length ? "amber" : "sage"}
                title={
                  unwatched.length
                    ? `${unwatched.length} area${unwatched.length === 1 ? "" : "s"} will not be watched`
                    : "Every area on the plan is watched"
                }
                body={
                  unwatched.length
                    ? `${unwatched.map((z) => z.replace(/_/g, " ")).join(", ")}. Nothing that happens there will reach you, and it will not be counted either. A fall in a watched area still reaches you whatever else you chose.`
                    : "The hallway, the kitchen, the door and the sofa. A fall in any of them reaches you."
                }
              />
              <hr className="hairline" />
              <p className="small" style={{ margin: 0 }}>
                Frames stay on the camera. When something does happen, a short clip
                goes to Aegis for analysis, is deleted from the model provider after
                one hour, and is deleted from this machine after seven days. Nothing
                is used to train anything. There is no face recognition, and the
                bathroom is masked before any frame is stored.
              </p>
            </div>
          </div>

          <div className="card" style={{ padding: 22, marginTop: 14, maxWidth: 660 }}>
            <p className="h3" style={{ margin: 0 }}>Margaret agrees to be watched</p>
            <p className="small" style={{ margin: "6px 0 14px", maxWidth: 520 }}>
              Consent has to come from the person being cared for, not only from the
              family. Type her name as she gave it. Aegis records who agreed, when,
              and the exact wording above.
            </p>
            <label style={{ display: "grid", gap: 6 }}>
              <span className="micro">Her name, in her words</span>
              <input
                className="input serif"
                style={{ fontSize: 18 }}
                value={consentName}
                onChange={(e) => setConsentName(e.target.value)}
                placeholder="Margaret"
                autoComplete="off"
              />
            </label>
          </div>

          {error ? (
            <p role="alert" className="small" style={{ color: "var(--color-red)", marginTop: 14 }}>
              {error}
            </p>
          ) : null}
        </section>
      ) : null}

      {/* footer controls */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          marginTop: 40,
          paddingTop: 20,
          borderTop: "1px solid var(--color-line)",
          maxWidth: 660,
        }}
      >
        <button
          className="btn btn-quiet"
          onClick={() => (index === 0 ? router.push("/") : go(-1))}
          disabled={saving}
        >
          <Icon name="back" size={17} />
          {index === 0 ? "Not now" : "Back"}
        </button>

        {step === "consent" ? (
          <button
            className="btn btn-primary"
            onClick={finish}
            disabled={saving || consentName.trim().length < 2}
          >
            {saving ? "Saving…" : "Start watching"}
            {saving ? null : <Icon name="arrow" size={17} />}
          </button>
        ) : (
          <button className="btn btn-primary" onClick={() => go(1)}>
            Continue
            <Icon name="arrow" size={17} />
          </button>
        )}
      </div>
    </Shell>
  );
}

/* ------------------------------------------------------------------ pieces */

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: "100dvh", background: "var(--color-paper)" }}>
      <div style={{ maxWidth: 900, margin: "0 auto", padding: "34px 22px 80px" }}>{children}</div>
    </div>
  );
}

function Toggle({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      style={{
        width: 46,
        height: 27,
        borderRadius: 999,
        border: "1px solid " + (on ? "var(--color-ink)" : "var(--color-line-2)"),
        background: on ? "var(--color-ink)" : "var(--color-paper-2)",
        padding: 2,
        cursor: "pointer",
        flex: "none",
        transition: "background 160ms ease-out",
      }}
    >
      <span
        style={{
          display: "block",
          width: 21,
          height: 21,
          borderRadius: 999,
          background: on ? "#fbf9f5" : "#fff",
          boxShadow: "0 1px 2px rgba(0,0,0,0.18)",
          transform: on ? "translateX(19px)" : "translateX(0)",
          transition: "transform 160ms ease-out",
        }}
      />
    </button>
  );
}

function AreaRow({ zone, on, onToggle }: { zone: Zone; on: boolean; onToggle: () => void }) {
  const kind =
    zone.type === "tripwire"
      ? "A line someone crosses"
      : zone.type === "hazard"
        ? "A place where things burn"
        : zone.type === "exclusion"
          ? "Masked before it leaves the camera"
          : zone.rest
            ? "Resting here is normal"
            : "Standing, walking, and falling";
  return (
    <div
      className="card"
      style={{
        padding: "14px 16px",
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: 14,
        alignItems: "center",
        opacity: zone.private ? 0.72 : 1,
      }}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <span style={{ color: "var(--color-ink-3)", marginTop: 2 }}>
          <Icon name={zone.private ? "shield" : zone.type === "tripwire" ? "door" : "camera"} size={17} />
        </span>
        <div>
          <p className="h3" style={{ margin: 0, textTransform: "capitalize" }}>
            {zone.name.replace(/_/g, " ")}
            {zone.private ? <span className="chip" style={{ marginLeft: 8 }}>never watched</span> : null}
            {zone.rest ? <span className="chip chip-sage" style={{ marginLeft: 8 }}>rest area</span> : null}
          </p>
          <p className="micro" style={{ margin: "3px 0 0" }}>{kind}</p>
        </div>
      </div>
      {zone.private ? (
        <span className="mono micro">locked</span>
      ) : (
        <Toggle on={on} onChange={onToggle} label={`Watch ${zone.name}`} />
      )}
    </div>
  );
}

function ClassCard({
  spec,
  mode,
  onMode,
}: {
  spec: WatchClass;
  mode: string;
  onMode: (mode: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card" style={{ padding: "16px 18px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 14 }}>
        <div style={{ minWidth: 0 }}>
          <p className="h3" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
            {spec.label}
            {spec.locked ? <span className="chip chip-red">always on</span> : null}
            {spec.tier === "comfort" ? <span className="chip">gentle</span> : null}
          </p>
          <p className="small" style={{ margin: "5px 0 0" }}>{spec.question}</p>
        </div>
        <button
          type="button"
          className="btn btn-quiet"
          style={{ minHeight: 30, padding: "0 6px" }}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={`Why ${spec.label} matters`}
        >
          <span style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 160ms ease-out", display: "inline-flex" }}>
            <Icon name="chevron" size={16} />
          </span>
        </button>
      </div>

      {open ? <p className="micro" style={{ margin: "10px 0 0" }}>{spec.detail}</p> : null}

      {spec.locked ? (
        <p className="micro" style={{ margin: "12px 0 0", color: "#8a2c24" }}>
          {spec.examples.alert}
        </p>
      ) : (
        <div
          role="radiogroup"
          aria-label={spec.label}
          style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginTop: 12 }}
        >
          {(["alert", "digest", "off"] as const).map((m) => {
            const active = mode === m;
            const copy = MODE_COPY[m];
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => onMode(m)}
                style={{
                  textAlign: "left",
                  padding: "9px 11px",
                  borderRadius: 10,
                  border: "1px solid " + (active ? "var(--color-ink)" : "var(--color-line)"),
                  background: active ? "var(--color-surface)" : "var(--color-paper-2)",
                  boxShadow: active ? "inset 0 0 0 1px var(--color-ink)" : "none",
                  cursor: "pointer",
                }}
              >
                <span className="h3" style={{ display: "block", fontSize: 13.5 }}>{copy.label}</span>
                <span className="micro" style={{ display: "block", marginTop: 2, lineHeight: 1.35 }}>
                  {copy.hint}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {!spec.locked && spec.examples[mode] ? (
        <p className="micro" style={{ margin: "10px 0 0", fontStyle: "italic" }}>{spec.examples[mode]}</p>
      ) : null}
    </div>
  );
}

function ContractLine({
  tone,
  title,
  body,
}: {
  tone: "red" | "sage" | "amber";
  title: string;
  body: string;
}) {
  const color = { red: "var(--color-red)", sage: "var(--color-sage)", amber: "var(--color-amber)" }[tone];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "3px 1fr", gap: 12 }}>
      <span style={{ background: color, borderRadius: 999 }} />
      <div>
        <p className="h3" style={{ margin: 0 }}>{title}</p>
        <p className="small" style={{ margin: "3px 0 0" }}>{body}</p>
      </div>
    </div>
  );
}
