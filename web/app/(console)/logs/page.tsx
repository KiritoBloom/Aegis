"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { useConsole } from "@/components/console-provider";
import type { LogEntry } from "@/lib/console-types";
import shared from "@/components/console-shared.module.css";
import styles from "@/components/console-pages.module.css";

type Tab = "all" | "system" | string;

function clockOf(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-GB", { hour12: false });
}

function LogLine({ entry, camera }: { entry: LogEntry; camera?: string }) {
  return (
    <div className={shared.logRow}>
      <span className={shared.logTime}>{clockOf(entry.at)}</span>
      <span className={shared.logSource} data-tone={entry.tone}>{entry.source}</span>
      <span className={shared.logMessage}>
        {entry.message}{entry.pipelineId ? <small className={styles.savedStep}>saved step</small> : null}
        {entry.detail ? <span className={shared.logDetail}>{entry.detail}</span> : null}
        {camera ? <span className={shared.logDetail}>{camera}</span> : null}
      </span>
    </div>
  );
}

export default function LogsPage() {
  const { feeds, logs, clearLogs, active, live, pipeline, pipelineError } = useConsole();
  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [savedOnly, setSavedOnly] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const groups = useMemo(() => {
    const list: {
      id: string;
      label: string;
      location: string;
      entries: LogEntry[];
      status: "idle" | "watching" | "ended" | "error" | null;
    }[] = feeds.map((feed) => ({
      id: feed.id,
      label: feed.name || feed.label,
      location: feed.location,
      entries: (logs[feed.id] ?? []).slice().reverse(),
      status: live[feed.id]?.status ?? null,
    }));
    list.push({
      id: "system",
      label: "System",
      location: "Cross-camera events",
      entries: (logs.system ?? []).slice().reverse(),
      status: null,
    });
    for (const id of Object.keys(logs)) {
      if (id === "system" || feeds.some((feed) => feed.id === id)) continue;
      list.push({ id, label: "Removed camera", location: id, entries: logs[id].slice().reverse(), status: null });
    }
    return list;
  }, [feeds, logs, live]);
  useEffect(() => { if (tab !== "all" && !groups.some((group) => group.id === tab)) setTab("all"); }, [groups, tab]);

  const visible = tab === "all"
    ? groups.filter((group) => group.entries.length)
    : groups.filter((group) => group.id === tab);

  const total = groups.reduce((sum, group) => sum + group.entries.length, 0);
  const rows = visible.flatMap((group) => group.entries.map((entry) => ({ entry, camera: group.label })))
    .filter(({ entry }) => (!savedOnly || entry.pipelineId) && (source === "all" || entry.source === source)
      && `${entry.message} ${entry.detail ?? ""}`.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => b.entry.at - a.entry.at || a.entry.id - b.entry.id);

  const exportLog = () => {
    const steps = pipeline.filter((step) => tab === "all" || step.feed_id === tab).map((step) => {
      const metrics = { ...step.metrics };
      delete metrics.audio_url; delete metrics.resident_url;
      return { ...step, metrics };
    });
    const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(),
      observations: rows.map(({ entry, camera }) => ({ ...entry, camera })), decision_steps: steps }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `aegis-evidence-${new Date().toISOString().slice(0, 10)}.json`; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const clear = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      window.setTimeout(() => setConfirmClear(false), 4000);
      return;
    }
    clearLogs(tab === "all" ? undefined : tab);
    setConfirmClear(false);
  };

  const chooseTab = (next: Tab) => {
    setTab(next);
    setConfirmClear(false);
  };

  return (
    <div className={styles.logsPage}>
      <div className={styles.logsHead}>
        <div className={shared.pageHead} style={{ borderBottom: 0, paddingBottom: 0 }}>
          <div>
            <p className={shared.kicker}>Observations → evidence → response</p>
             <h1 className={shared.pageTitle}>Camera diagnostics</h1>
            <p className={shared.pageLede}>
               Follow each observation, model request, decision, and response in order. Decision steps are saved on the server; camera telemetry stays in this browser.
            </p>
          </div>
          <div className={shared.headStat}>
            <strong>{total}</strong>
            <span>{pipeline.length} saved steps</span>
          </div>
        </div>

        <div className={shared.toolbar}>
          <span className={shared.homeStat}>
            {active ? "Analysis is running" : "Analysis is stopped"}
          </span>
          <span style={{ flex: 1 }} />
          <Link className={shared.smallButton} href="/events?view=activity">Household activity log</Link>
          <button type="button" className={shared.smallButton} onClick={exportLog} disabled={!rows.length}>Export evidence</button>
          <button type="button" className={shared.ghostButton} onClick={clear}
            disabled={!total}>
            {confirmClear ? "Confirm clear telemetry" : "Clear browser telemetry"}
          </button>
        </div>

        <div className={styles.feedTabs} role="group" aria-label="Log by camera">
          <button type="button" className={styles.feedTab} data-on={tab === "all"} aria-pressed={tab === "all"} onClick={() => chooseTab("all")}>
            All views <span>{total}</span>
          </button>
          {feeds.map((feed) => (
            <button
              key={feed.id}
              type="button"
              className={styles.feedTab}
               data-on={tab === feed.id}
               aria-pressed={tab === feed.id}
              onClick={() => chooseTab(feed.id)}
            >
              {feed.name || feed.label}
              <span>{(logs[feed.id] ?? []).length}</span>
            </button>
          ))}
          <button type="button" className={styles.feedTab} data-on={tab === "system"} aria-pressed={tab === "system"} onClick={() => chooseTab("system")}>
            System <span>{(logs.system ?? []).length}</span>
          </button>
        </div>
      </div>

      <div className={styles.logStream}>
        <div className={styles.diagnosticFilters}>
          <label>Search the trail<input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Posture, evidence, response…" /></label>
          <label>Source<select value={source} onChange={(e) => setSource(e.target.value)}><option value="all">All sources</option>{["Vision", "Model", "Alert", "System"].map((item) => <option key={item}>{item}</option>)}</select></label>
          <button className={shared.smallButton} aria-pressed={savedOnly} onClick={() => setSavedOnly(!savedOnly)}>{savedOnly ? "Showing saved decisions" : "Saved decisions only"}</button>
        </div>
        {pipelineError ? <p className={shared.logEmpty}>Saved steps could not load. Browser telemetry remains available.</p> : null}
        {rows.length ? <section className={styles.logGroup}><div className={styles.logGroupHead}><h3>{tab === "all" ? "All cameras · unified timeline" : visible[0]?.label}</h3><span>{rows.length} rows · newest first</span></div>
          {rows.map(({ entry, camera }) => <LogLine key={`${camera}-${entry.id}`} entry={entry} camera={tab === "all" ? camera : undefined} />)}
        </section> : (
          <div className={shared.emptyState}>
            <span className={shared.emptyStateMark}><Icon name="logs" size={17} /></span>
            <h2>{total ? "No matching steps." : "No rows yet."}</h2>
            <p>
              {active
                ? "The console is watching. Tracking, posture and verification rows will appear here as the feeds are analyzed."
                : "Activate the system from the top bar. Local tracking rows begin as soon as a feed is open."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
