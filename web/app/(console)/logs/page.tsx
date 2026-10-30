"use client";

import { useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { useConsole } from "@/components/console-provider";
import type { LogEntry } from "@/lib/console-types";
import shared from "@/components/console-shared.module.css";
import styles from "@/components/console-pages.module.css";

type Tab = "all" | "system" | string;

function clockOf(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-GB", { hour12: false });
}

function LogLine({ entry }: { entry: LogEntry }) {
  return (
    <div className={shared.logRow}>
      <span className={shared.logTime}>{clockOf(entry.at)}</span>
      <span className={shared.logSource} data-tone={entry.tone}>{entry.source}</span>
      <span className={shared.logMessage}>
        {entry.message}
        {entry.detail ? <span className={shared.logDetail}>{entry.detail}</span> : null}
      </span>
    </div>
  );
}

export default function LogsPage() {
  const { feeds, logs, clearLogs, active, live } = useConsole();
  const [tab, setTab] = useState<Tab>("all");
  const [confirmClear, setConfirmClear] = useState(false);

  const groups = useMemo(() => {
    const list: {
      id: string;
      label: string;
      location: string;
      entries: LogEntry[];
      status: "idle" | "watching" | "error" | null;
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
    return list;
  }, [feeds, logs, live]);

  const visible = tab === "all"
    ? groups.filter((group) => group.entries.length)
    : groups.filter((group) => group.id === tab);

  const total = groups.reduce((sum, group) => sum + group.entries.length, 0);

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
            <p className={shared.kicker}>Local telemetry</p>
            <h1 className={shared.pageTitle}>What each camera actually did.</h1>
            <p className={shared.pageLede}>
              Local tracking, posture changes, verification and alert transport — one stream per
              camera. These rows are written in this browser and are not part of the family record.
            </p>
          </div>
          <div className={shared.headStat}>
            <strong>{total}</strong>
            <span>local rows</span>
          </div>
        </div>

        <div className={shared.toolbar}>
          <span className={shared.homeStat}>
            {active ? "Analysis is running" : "Analysis is stopped"}
          </span>
          <span style={{ flex: 1 }} />
          <button type="button" className={shared.ghostButton} onClick={clear}
            disabled={!total}>
            {confirmClear ? "Confirm clear" : tab === "all" ? "Clear all logs" : "Clear this log"}
          </button>
        </div>

        <div className={styles.feedTabs} role="tablist" aria-label="Log by camera">
          <button type="button" className={styles.feedTab} data-on={tab === "all"} onClick={() => chooseTab("all")}>
            All views <span>{total}</span>
          </button>
          {feeds.map((feed) => (
            <button
              key={feed.id}
              type="button"
              className={styles.feedTab}
              data-on={tab === feed.id}
              onClick={() => chooseTab(feed.id)}
            >
              {feed.name || feed.label}
              <span>{(logs[feed.id] ?? []).length}</span>
            </button>
          ))}
          <button type="button" className={styles.feedTab} data-on={tab === "system"} onClick={() => chooseTab("system")}>
            System <span>{(logs.system ?? []).length}</span>
          </button>
        </div>
      </div>

      <div className={styles.logStream}>
        {visible.length ? visible.map((group) => (
          <section className={styles.logGroup} key={group.id}>
            <div className={styles.logGroupHead}>
              <h3>
                {group.label}
                {group.location ? ` · ${group.location}` : ""}
              </h3>
              <span>
                {group.status === "watching" ? "LIVE" : group.status === "error" ? "LINK LOST" : group.id === "system" ? "ALL CAMERAS" : "STANDBY"}
                {" · "}
                {group.entries.length} rows
              </span>
            </div>
            {group.entries.length ? (
              group.entries.map((entry) => <LogLine key={`${group.id}-${entry.id}`} entry={entry} />)
            ) : (
              <p className={shared.logEmpty}>No local activity on this view yet.</p>
            )}
          </section>
        )) : (
          <div className={shared.emptyState}>
            <span className={shared.emptyStateMark}><Icon name="logs" size={17} /></span>
            <h2>No rows yet.</h2>
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
