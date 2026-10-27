# Aegis check-in eval

Does the voice check-in change an outcome the way the plan claims?

Every transcript below came back from `muse-voice-transcribe-1.0` on
synthesised speech, not from a string typed into the test. The
classifier and the de-escalation table then run on that real output.

**5/5 cases behaved correctly.**

| Case | She said | Transcript | Class | Perception | Outcome | Delta |
|---|---|---|---|---|---|---|
| contradiction_floor | I'm fine, I dropped the remote. | I'm fine. I dropped the remote. | `affirmative` | upright | `contradiction` | +0 |
| contradiction_still | I am okay, really. | I am okay. Really. | `affirmative` | upright | `contradiction` | +0 |
| negated_affirmative | I'm not okay, I can't get up. | I'm not okay, I can't get up. | `help` | upright | `escalated` | +1 |
| help_explicit | Please help me, I need help. | Please help me, me, I need help. | `help` | upright | `escalated` | +1 |
| self_recovered | I'm fine, I just tripped. I'm up now. | I'm fine. I just tripped. I'm I'm up now. | `affirmative` | upright | `self_recovered` | -1 |

## Why each case exists

- **contradiction_floor** — Says she is fine from the floor. The camera disagrees. Must not resolve.
- **contradiction_still** — Motionless for 22s while claiming to be fine is the same contradiction.
- **negated_affirmative** — Contains the word 'okay'. Negation must be read first.
- **help_explicit** — The highest-value word in the system escalates even if she looks calm.
- **self_recovered** — The only path down: she says fine AND she is on her feet and moving.

## The rule under test

One channel can never lower an alert. Two agreeing channels can.

- `affirmative` + upright and moving is the **only** path to de-escalation.
- `affirmative` from the floor, or while motionless, is a **contradiction**:
  severity is unchanged and the alert page says she said she is fine but
  has not stood up.
- `help` escalates one tier even if perception looks calm. It is the
  highest-value word in the system.
- `no_response` escalates after 20s. Silence is data.
- `confused` / `other` changes nothing and is marked for the family.

## Limits

Five cases, five real ASR calls. This is a regression suite for the
de-escalation table, not a measure of speech recognition accuracy in a
noisy hallway. The audio is clean synthetic speech, which is the easiest
possible input for ASR; a real kitchen at 3 AM is not.
