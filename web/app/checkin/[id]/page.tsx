"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "@/lib/useApi";
import { clock, type Incident } from "@/lib/api";
import { canRecord, startRecording, type Recorder } from "@/lib/recorder";
import styles from "./checkin.module.css";

/** Long enough to say a sentence, short enough that nobody holds it forever. */
const MAX_VOICE_S = 10;

type Answer = {
  transcript: string;
  class: string;
  classifier_class?: string;
  severity: string;
  previous: string;
  contradiction: boolean;
  resolution: string;
  reason: string;
  read?: { read: string } | null;
  model: string;
};

/** Resident-facing check-in. Large controls only; never the family alert history. */
export default function CheckinRoute() {
  return (
    <Suspense fallback={null}>
      <ResidentCheckinPage />
    </Suspense>
  );
}

function ResidentCheckinPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const id = params?.id ?? "";
  const token = searchParams.get("t") || "";
  const apiPath = token
    ? `/api/resident/checkin/${id}?t=${encodeURIComponent(token)}`
    : `/api/incidents/${id}`;
  const { data: inc, error, loading, reload } = useApi<Incident>(apiPath, 5000);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [busy, setBusy] = useState<"ok" | "help" | "voice" | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState(0);
  const recorderRef = useRef<Recorder | null>(null);
  const listenStopRef = useRef<number | null>(null);

  const pending = inc?.checkin_status === "asking";
  const seconds = inc?.checkin_due_at
    ? Math.max(0, Math.ceil(inc.checkin_due_at - now / 1000))
    : null;

  useEffect(() => {
    if (!pending) return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [pending]);

  const prompt = inc?.checkin_prompt || "Are you okay? Say I'm okay, or say help.";
  const time = useMemo(() => {
    const at = inc?.local_time || inc?.created_at;
    return at ? clock(at) : "";
  }, [inc?.local_time, inc?.created_at]);

  const speak = () => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const utterance = new SpeechSynthesisUtterance(prompt);
    utterance.rate = 0.9;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  };

  const post = useCallback(
    async (body: BodyInit, contentType: string): Promise<Answer> => {
      const res = await fetch(
        token
          ? `/api/resident/checkin/${id}?t=${encodeURIComponent(token)}`
          : `/api/incidents/${id}/checkin`,
        { method: "POST", headers: { "content-type": contentType }, body },
      );
      const text = await res.text();
      if (!res.ok) {
        let detail = `Could not send the answer (${res.status})`;
        try {
          detail = (JSON.parse(text) as { detail?: string }).detail || detail;
        } catch {
          /* keep the default */
        }
        throw new Error(detail);
      }
      return JSON.parse(text) as Answer;
    },
    [id, token],
  );

  const send = async (kind: "ok" | "help") => {
    setBusy(kind);
    setSendError(null);
    try {
      setAnswer(
        await post(
          JSON.stringify({ answer: kind === "ok" ? "I'm okay" : "I need help" }),
          "application/json",
        ),
      );
      reload();
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "Could not send the answer.");
    } finally {
      setBusy(null);
    }
  };

  const stopListening = useCallback(() => {
    if (listenStopRef.current !== null) {
      window.clearTimeout(listenStopRef.current);
      listenStopRef.current = null;
    }
    setListening(false);
  }, []);

  // Speak the answer instead of tapping: the backend transcribes it, and a
  // transcript can escalate ("help") or clear the alert in a way a fixed
  // button cannot. The recording is deleted after transcription.
  const finishVoice = async () => {
    const rec = recorderRef.current;
    recorderRef.current = null;
    stopListening();
    if (!rec) return;
    setBusy("voice");
    try {
      const wav = await rec.stop();
      setAnswer(await post(wav, "audio/wav"));
      reload();
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "Could not send the answer.");
    } finally {
      setBusy(null);
    }
  };

  const sendVoice = async () => {
    if (busy || listening) return;
    if (!canRecord()) {
      setSendError("This device cannot record audio. Use the buttons instead.");
      return;
    }
    setSendError(null);
    setHeard(0);
    try {
      const rec = await startRecording((seconds) => setHeard(seconds));
      recorderRef.current = rec;
      setListening(true);
      listenStopRef.current = window.setTimeout(() => {
        listenStopRef.current = null;
        void finishVoice();
      }, MAX_VOICE_S * 1000);
    } catch {
      setSendError("The microphone could not be opened. Use the buttons instead.");
      stopListening();
    }
  };

  const cancelVoice = () => {
    recorderRef.current?.cancel();
    recorderRef.current = null;
    stopListening();
  };

  useEffect(() => () => {
    recorderRef.current?.cancel();
    recorderRef.current = null;
    if (listenStopRef.current !== null) window.clearTimeout(listenStopRef.current);
  }, []);

  if (error) {
    return (
      <main className={styles.screen}>
        <div className={styles.frame}>
          <Header />
          <div className={styles.state}>
            <p className={styles.kicker}>Aegis check-in</p>
            <h1 className={styles.title}>This question is not available.</h1>
            <p className={styles.body}>
              Aegis could not read this check-in. If you need help, call your family or your
              local emergency number.
            </p>
          </div>
        </div>
      </main>
    );
  }

  if (loading && !inc) {
    return (
      <main className={styles.screen}>
        <div className={styles.frame}>
          <Header />
          <div className={styles.state}>
            <div className={styles.loader} aria-label="Loading check-in" />
          </div>
        </div>
      </main>
    );
  }

  if (!inc) {
    return (
      <main className={styles.screen}>
        <div className={styles.frame}>
          <Header />
          <div className={styles.state}>
            <p className={styles.kicker}>Aegis check-in</p>
            <h1 className={styles.title}>Nothing is being asked right now.</h1>
            <p className={styles.body}>There is no check-in waiting for an answer.</p>
          </div>
        </div>
      </main>
    );
  }

  const answered = answer || inc.checkin_status === "answered";
  const timedOut = inc.checkin_status === "no_answer" && !answer;
  const closed = !pending && !answered && !timedOut;
  const resolution = answer?.resolution ?? inc.checkin?.outcome ?? "";

  if (closed) {
    return (
      <main className={styles.screen}>
        <div className={styles.frame}>
          <Header />
          <div className={styles.state}>
            <p className={styles.kicker}>Aegis check-in</p>
            <h1 className={styles.title}>
              {inc.checkin_status === "cancelled"
                ? "This check-in was closed."
                : "Nothing is being asked right now."}
            </h1>
            <p className={styles.body}>
              {inc.checkin_status === "cancelled"
                ? "Monitoring was paused or the question ended. Aegis will not ask again for this alert."
                : "There is no check-in waiting for an answer."}
            </p>
            <Link href="/" className={styles.homeLink}>Back to the house</Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className={styles.screen}>
      <div className={styles.frame}>
        <Header time={time} />

        {pending && !answered ? (
          <section className={styles.stage} aria-labelledby="checkin-question">
            <p className={styles.kicker}>Aegis is asking you</p>
            <h1 id="checkin-question" className={styles.question}>
              &ldquo;{prompt}&rdquo;
            </h1>

            <div className={styles.speakRow}>
              {inc.checkin_audio_url ? (
                <audio className={styles.audio} src={inc.checkin_audio_url} controls autoPlay />
              ) : (
                <button type="button" className={styles.speakButton} onClick={speak}>
                  Hear the question
                </button>
              )}
              <span className={styles.speakHint}>
                {seconds !== null ? `Your family will be alerted in ${seconds}s if you do not answer.` : "The question is spoken in the room."}
              </span>
            </div>

            <div className={styles.timer} aria-hidden="true">
              <span
                className={styles.timerFill}
                style={{ width: inc.checkin_due_at ? `${Math.max(0, Math.min(100, ((inc.checkin_due_at - now / 1000) / (inc.checkin_timeout_s || 15)) * 100))}%` : "100%" }}
              />
            </div>

            <div className={styles.actions}>
              <button
                type="button"
                className={styles.okButton}
                onClick={() => void send("ok")}
                disabled={busy !== null || listening}
              >
                <span className={styles.okMark} aria-hidden="true">✓</span>
                <span>
                  <strong>{busy === "ok" ? "Sending…" : "I’m okay"}</strong>
                  <small>I can stay where I am</small>
                </span>
              </button>
              <button
                type="button"
                className={styles.helpButton}
                onClick={() => void send("help")}
                disabled={busy !== null || listening}
              >
                <span className={styles.helpMark} aria-hidden="true">!</span>
                <span>
                  <strong>{busy === "help" ? "Sending…" : "I need help"}</strong>
                  <small>Please tell my family</small>
                </span>
              </button>
            </div>

            <div className={styles.voiceRow}>
              {listening ? (
                <>
                  <button type="button" className={styles.voiceButton} onClick={() => void finishVoice()}>
                    <span className={styles.voiceDot} aria-hidden="true" />
                    Listening · tap when you are done
                  </button>
                  <button type="button" className={styles.voiceCancel} onClick={cancelVoice}>
                    Cancel
                  </button>
                  <span className={styles.speakHint}>
                    {heard < 1 ? "Say “I’m okay” or “help”." : `${Math.round(heard)}s heard.`}
                  </span>
                </>
              ) : (
                <button
                  type="button"
                  className={styles.voiceButton}
                  onClick={() => void sendVoice()}
                  disabled={busy !== null}
                >
                  {busy === "voice" ? "Sending your answer…" : "Say it instead"}
                </button>
              )}
            </div>

            {sendError ? <p className={styles.error} role="alert">{sendError}</p> : null}

            <p className={styles.foot}>
              Your answer is recorded only for this check-in. If you are in danger, call your local
              emergency number.
            </p>
          </section>
        ) : (
          <section className={styles.stage} aria-live="polite">
            <p className={styles.kicker}>{timedOut ? "The check-in ended" : "Thank you"}</p>
            <h1 className={styles.title}>
              {timedOut
                ? "Your family has been told."
                : resolution === "self_recovered"
                  ? "Your family knows you answered."
                  : resolution === "escalated"
                    ? "Your family is being told now."
                    : "Your answer was recorded."}
            </h1>
            <p className={styles.body}>
              {timedOut
                ? "No answer was heard before the deadline. Aegis escalated the alert so someone can check on you."
                : answer?.contradiction
                  ? "Aegis heard that you are okay, but the camera still sees something it does not understand. The alert stays open."
                  : resolution === "self_recovered"
                    ? "You said you are okay and the camera agrees you are on your feet. Nothing more is needed."
                    : resolution === "escalated"
                      ? "Aegis sent the answer to your family. They can call or come to you."
                      : "Someone can still check the answer. The alert has not been silently dismissed."}
            </p>
            {answer?.read?.read ? <p className={styles.quote}>“{answer.read.read}”</p> : null}
            <Link href="/" className={styles.homeLink}>
              Back to the house
            </Link>
          </section>
        )}
      </div>
    </main>
  );
}

function Header({ time }: { time?: string }) {
  return (
    <header className={styles.header}>
      <span className={styles.wordmark}>Aegis</span>
      <span className={styles.headerMeta}>
        <span className={styles.liveDot} aria-hidden="true" />
        {time ? `check-in · ${time}` : "check-in"}
      </span>
    </header>
  );
}
