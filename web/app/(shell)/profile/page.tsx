"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { Banner, Fact, Skeleton } from "@/components/ui";
import { Notifications } from "@/components/notifications";
import { hourLabel, send, type Household } from "@/lib/api";
import { useApi } from "@/lib/useApi";

/** Household profile and the push subscription that makes alerts deliverable. */
export default function ProfilePage() {
  const household = useApi<Household>("/api/household", 30000);
  const [profile, setProfile] = useState<Household["profile"] | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (household.data && !profile) setProfile(household.data.profile);
  }, [household.data, profile]);

  const dirty =
    profile && household.data ? JSON.stringify(profile) !== JSON.stringify(household.data.profile) : false;

  const save = async () => {
    if (!profile) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      await send("/api/household", { profile }, "PUT");
      setSaved("Saved. The next alert uses these answers.");
      household.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  if (household.error) {
    return (
      <div style={{ maxWidth: 720 }}>
        <p className="eyebrow">Household</p>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>Not reachable.</h1>
        <p className="mono micro" style={{ marginTop: 12 }}>{household.error}</p>
      </div>
    );
  }

  if (!profile || !household.data) {
    return (
      <div style={{ maxWidth: 820, display: "grid", gap: 12 }}>
        <Skeleton h={34} w={280} />
        <Skeleton h={200} r={12} />
      </div>
    );
  }

  const watch = household.data.watch;
  const paused = household.data.privacy.pause.paused;

  return (
    <div style={{ maxWidth: 860 }}>
      <header style={{ marginBottom: 22 }}>
        <p className="eyebrow">Household</p>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>{profile.display_name}&apos;s home.</h1>
        <p className="lede" style={{ marginTop: 10, maxWidth: 560 }}>
          These answers change what an alert says and how quickly it escalates. They
          are not stored at a model provider; they live in this house.
        </p>
      </header>

      {saved ? <div style={{ marginBottom: 14 }}><Banner tone="sage">{saved}</Banner></div> : null}
      {error ? <div style={{ marginBottom: 14 }}><Banner tone="red">{error}</Banner></div> : null}

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "1fr" }} className="lg:grid-cols-[1.2fr_1fr]">
        <section className="card" style={{ padding: 20 }}>
          <p className="h3" style={{ margin: 0 }}>Care profile</p>

          <div style={{ display: "grid", gap: 14, marginTop: 16 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label style={{ display: "grid", gap: 6 }}>
                <span className="micro">Name</span>
                <input
                  className="input"
                  value={profile.display_name}
                  onChange={(e) => setProfile({ ...profile, display_name: e.target.value })}
                />
              </label>
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
            </div>

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

            <div style={{ display: "grid", gap: 8 }}>
              {(
                [
                  ["lives_alone", "Lives alone"],
                  ["wanders", "Has wandered before"],
                  ["anticoagulant", "Takes a blood thinner"],
                  ["uses_mobility_aid", "Uses a stick or frame"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className="choice"
                  aria-checked={Boolean(profile[key])}
                  onClick={() => setProfile({ ...profile, [key]: !profile[key] })}
                >
                  <span className="radio" />
                  <span className="h3">{label}</span>
                </button>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 18, alignItems: "center" }}>
            <button className="btn btn-primary" onClick={save} disabled={!dirty || saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            {dirty ? <span className="micro">Unsaved changes</span> : null}
          </div>
        </section>

        <section style={{ display: "grid", gap: 14, alignContent: "start" }}>
          <div className="card" style={{ padding: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
              <p className="h3" style={{ margin: 0 }}>The plan</p>
              <span className="mono micro">{household.data.onboarded ? "set" : "default"}</span>
            </div>
            <div style={{ marginTop: 8 }}>
              <Fact label="Always alerts" value={watch.locked.length} />
              <Fact label="Interrupts you" value={watch.alert.length} />
              <Fact label="Morning letter" value={watch.digest.length} />
              <Fact label="Never shown" value={watch.off.length} />
              <Fact
                label="Quiet hours"
                value={watch.quiet.enabled ? `${hourLabel(watch.quiet.start)}–${hourLabel(watch.quiet.end)}` : "off"}
              />
              <Fact label="Front door" value={watch.door_hours === "night" ? "night only" : "any time"} />
              <Fact
                label="Areas not watched"
                value={
                  watch.zones_off && watch.zones_off.length > 0
                    ? watch.zones_off.map((z) => z.replace(/_/g, " ")).join(", ")
                    : "none"
                }
              />
            </div>
            <Link
              href="/setup"
              className="btn"
              style={{ width: "100%", marginTop: 12, justifyContent: "space-between" }}
            >
              Change what alerts me
              <Icon name="arrow" size={16} />
            </Link>
          </div>

          <div className="card" style={{ padding: 20 }}>
            <p className="h3" style={{ margin: 0 }}>Consent</p>
            <div style={{ marginTop: 8 }}>
              <Fact label="Agreed" value={household.data.privacy.consent.consented ? "yes" : "not yet"} />
              <Fact label="By" value={household.data.privacy.consent.member || "—"} />
              <Fact
                label="Version"
                value={household.data.privacy.consent.version || "—"}
              />
              <Fact label="Monitoring" value={paused ? "paused" : "running"} />
            </div>            <Link href="/privacy" className="mono micro" style={{ display: "inline-block", marginTop: 10, textDecoration: "underline" }}>
              Privacy and control →
            </Link>
          </div>

          <Notifications memberId={household.data.profile.display_name} />

          <div className="card-flat" style={{ padding: 16 }}>
            <p className="eyebrow" style={{ margin: 0 }}>Caregiver mode</p>
            <p className="small" style={{ margin: "6px 0 0" }}>
              When a caregiver is in the house, door opens and movement are expected and
              do not escalate. Aegis does not identify people by face; it asks whether a
              second person is present, which is a different question.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
