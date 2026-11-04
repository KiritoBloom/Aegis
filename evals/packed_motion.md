# Text-only motion interpretation from SAM masks

Feasibility experiment, 2026-11-04. The capability tested is visual-action
interpretation through a **text-only request**, without image/video attachments,
tool execution, a trained action classifier, or earlier model decisions.

## What Muse actually receives

Hosted SAM first segments the person in an acted recording. Each sampled mask
is reduced to a 32×18 binary grid. Each row becomes one eight-digit hexadecimal
integer. A request contains 24 such grids plus timestamps and a description of
the bit layout. It contains no source filename, action label or rendered image.

The occupancy itself is 72 bytes per observation, or **1,728 bytes for 24
observations**. The actual JSON packet in the first case is **5,778 bytes**;
instructions/schema/transport overhead are additional. This is not a claim
that the full Meta request or original SAM upload is 1,728 bytes.

Muse Spark interprets the changing occupied regions and returns a structured
action, visible-motion explanation and cited frame numbers. For the first
fall, it described a tall narrow region becoming a low horizontal region and
remaining there. No precomputed height, speed, posture or fall score was sent.

## Input-only controls

The same first-case bitmaps were transformed without telling Muse which
transformation was applied. Timestamps were reassigned in increasing order.

| Input | Expected before inference | Actual response |
|---|---|---|
| Original fall sequence | Fall | Fall |
| Sequence reversed | Getting up | Getting up |
| Final low pose repeated 24 times | Uncertain: no movement | Uncertain: no movement |

For the frozen input, the actual response explained that the occupancy was
identical throughout and that a low static posture alone does not indicate a
fall. These are sanity checks against order-insensitive/low-posture responses,
not proof of internal reasoning or a representative accuracy evaluation.

## Completed eight-case comparison

All eight cases completed successfully. Exact action labels matched on **7/8**:
all three labelled falls, walking, exercise, getting up and controlled lowering
onto the floor. Controlled lowering onto the bed was incorrectly called a fall.

| Muse input | Exact action matches | Labelled falls called fall | Non-falls called fall |
|---|---:|---:|---:|
| Original RGB contact sheet | 6/8 | 1/3 | 0/5 |
| 32×18 binary-mask contact sheet | 6/8 | 3/3 | 1/5 |
| Text-only hexadecimal masks | 7/8 | 3/3 | 1/5 |

These are single calls on eight preselected acted clips, not evidence that the
text condition is generally more accurate. The original six completed text
responses were cached; after the first runner hit its process deadline, only
the two unfinished cases were resumed. No response was selected from multiple
completed attempts.

The comparison is recorded in [`packed_motion.json`](packed_motion.json),
including exact requests and the false fall. The original RGB and rendered-mask
comparison is documented in [`motion_capability.md`](motion_capability.md).
The text-only result must not be represented as a safe replacement for the
resident-first evidence gate.

## Applicable demo

In Aegis, show the actual recording, then its tiny SAM masks, then the exact
hexadecimal packet and Muse's answer. Reverse the sequence and freeze it to
demonstrate how the outputs change. The pitch is **“motion understanding after
removing appearance—even through a text-only model request.”**

SAM still receives source footage. This limits the evidence subsequently sent
to Muse; it does not establish local-only processing, anonymity, clinical
accuracy or general sign-language/physiological understanding. Dataset labels
describe acted clips, not measured intent or clinical distress. No claim is
made that this is a world first or unknown to Meta's researchers.

## Reproduce

```sh
python evals/experiments/motion_capability.py
python evals/experiments/packed_motion.py
python evals/experiments/motion_order_control.py
```

Use the pinned environment, Meta credentials and the existing licensed corpus.
Completed matching requests are cached under `.tmp/motion-capability`.
Source/environment provenance and exact requests are in the reports; input
order controls are in `motion_order_control.json`. Research runners do not
change the running Aegis alert pipeline.
