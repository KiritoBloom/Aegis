const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "../lib/inspection.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const helpers = {};
new Function("exports", compiled)(helpers);
const { inspectionJobs, inspectionMedia, inspectionStage, inspectionLiveStage, inspectionVideoState } = helpers;
const row = (id, metrics, status = "running", stage = "muse") => ({
  id, incident_id: "inc_demo", feed_id: "feed_demo", feed_name: "Hallway", stage,
  status, title: "Actual pipeline step", detail: "Returned summary", created_at: `2026-11-04T12:00:0${id}Z`, metrics,
});

test("keeps the running request with its completed response, despite null completion fields", () => {
  const jobs = inspectionJobs([
    row(1, { phase: "decision", request: { packet: { posture: "lying" } } }),
    row(2, { phase: "decision", request: null, response: { verdict: "escalate" } }, "done"),
  ]);
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].id, 1);
  assert.equal(jobs[0].status, "done");
  assert.deepEqual(jobs[0].metrics.request, { packet: { posture: "lying" } });
  assert.deepEqual(jobs[0].metrics.response, { verdict: "escalate" });
});

test("concurrent tools, different visual questions, and follow-up plans remain separate", () => {
  const jobs = inspectionJobs([
    row(1, { phase: "tool", tool: "household_memory", question: "" }),
    row(2, { phase: "tool", tool: "recent_events", question: "" }),
    row(3, { phase: "tool", tool: "inspect_frame", question: "Floor?" }),
    row(4, { phase: "tool", tool: "inspect_frame", question: "Bed?" }),
    row(5, { phase: "plan" }), row(6, { phase: "plan", follow_up: true }),
    row(7, { phase: "tool", tool: "household_memory", question: "", response: { facts: [] } }, "done"),
  ]);
  assert.equal(jobs.length, 6);
  assert.equal(jobs[0].status, "done");
  assert.equal(jobs[1].status, "running");
});

test("failed completion stays failed and never fabricates a model response", () => {
  const jobs = inspectionJobs([row(1, { phase: "decision", request: { packet: {} } }), row(2, { phase: "decision" }, "failed")]);
  assert.equal(jobs[0].status, "failed");
  assert.equal(jobs[0].metrics.response, undefined);
  assert.deepEqual(jobs[0].metrics.request, { packet: {} });
});

test("a newly streamed artifact is visible without waiting for the diagnostics poll", () => {
  const saved = [{ name: "sam_input.mp4", url: "/input", kind: "video" }];
  const streamed = { name: "annotated.mp4", url: "/segmentation", kind: "video" };
  assert.deepEqual(inspectionMedia(saved, [row(1, { media: [streamed] }, "done", "sam")]), [...saved, streamed]);
  assert.equal(inspectionStage("delivery"), "checkin");
  assert.equal(inspectionStage("privacy"), "privacy");
});

test("missing evidence never waits forever after completion or a connection failure", () => {
  const finished = [row(1, { phase: "decision" }, "done")];
  assert.equal(inspectionVideoState("annotated.mp4", [], finished), "Not recorded");
  assert.equal(inspectionVideoState("privacy.mp4", [], finished, "error"), "Connection error");
  assert.equal(inspectionVideoState("privacy.mp4", [], finished, "loading"), "Loading");
  const rendering = row(2, { phase: "video" }, "running", "privacy");
  assert.equal(inspectionVideoState("privacy.mp4", [], [...finished, rendering]), "Processing");
  assert.equal(inspectionVideoState("privacy.mp4", [], [row(3, { phase: "video" }, "failed", "privacy")]), "Unavailable");
  assert.equal(inspectionVideoState("privacy.mp4", [{ name: "privacy.mp4" }], finished, "error"), "Ready to watch ↗");
});

test("live focus stays on the decision/response when a background preview or summary arrives", () => {
  const rows = [row(1, { phase: "decision" }), row(2, { phase: "video" }, "done", "privacy")];
  assert.equal(inspectionLiveStage(rows), "muse");
  rows.push(row(3, {}, "running", "checkin"), row(4, { phase: "verified" }, "done"));
  assert.equal(inspectionLiveStage(rows), "checkin");
});

test("completed resident check-in replaces its waiting status and keeps the prompt link", () => {
  const start = { ...row(1, { resident_url: "/checkin/inc_demo" }, "running", "checkin"), title: "Waiting for an answer" };
  const end = { ...row(2, {}, "done", "checkin"), title: "Check-in expired" };
  const jobs = inspectionJobs([start, end]);
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].status, "done");
  assert.equal(jobs[0].title, "Check-in expired");
  assert.equal(jobs[0].metrics.resident_url, "/checkin/inc_demo");
});
