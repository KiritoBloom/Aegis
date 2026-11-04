# Motion understanding after removing source-image appearance

Local experiment, 2026-11-04. This is a feasibility result, not a production
change or a field-accuracy claim. Exact inputs, responses, source hashes and
environment provenance: [`motion_capability.json`](motion_capability.json).

## Question

Can hosted Muse Spark recognise movement from extremely coarse binary masks
returned by hosted SAM, rather than seeing the person's appearance or room?

Eight acted recordings were selected before inference: three labelled falls
(including a bed fall), controlled lying on a bed, controlled lying on the
floor, walking, exercise and getting up. Every condition used the same 24
time-ordered observations. Labels, source filenames and other conditions'
verdicts were excluded from the requests.

1. Prepare each recording at 768×432, six frames per second, without audio.
2. Ask `sam-3.1` for `person`; decode actual returned masks.
3. Compare original RGB observations with 64×36 and 32×18 binary occupancy.
4. Ask `muse-spark-1.3` to classify each sequence once, with an explicit
   `uncertain` option. No trained classifier, tool loop or policy validator.

All eight clips had SAM masks on every prepared frame. Binary observations
were enlarged into readable contact sheets for the image API. **72 bytes is
the packed occupancy information in one 32×18 frame, not the PNG/API payload
size.** Timestamps and frame numbers were included.

## Results

| Input to Muse | Exact action matches | Labelled falls called fall | Non-falls called fall | Uncertain |
|---|---:|---:|---:|---:|
| Original RGB | 6/8 | 1/3 | 0/5 | 0 |
| 64×36 binary masks | 6/8 | 3/3 | 2/5 | 0 |
| 32×18 binary masks | 6/8 | 3/3 | 1/5 | 1 |

| Recording | Reference | RGB | 64×36 | 32×18 |
|---|---|---|---|---|
| gmd-sub3-fall-05 | Fall | Fall | Fall | Fall |
| gmdcsa24-fall-10 | Fall | Controlled lowering | Fall | Fall |
| gmd-sub2-fall-03 | Bed fall | Controlled lowering | Fall | Fall |
| gmdcsa24-adl-01 | Controlled bed lowering | Controlled lowering | Fall | Fall |
| gmdcsa24-adl-08 | Walking | Walking | Walking | Walking |
| gmd-sub3-adl-05 | Exercise | Exercise | Exercise | Exercise |
| gmd-sub3-adl-08 | Controlled floor lowering | Controlled lowering | Fall | Uncertain |
| gmd-sub3-adl-09 | Getting up | Getting up | Getting up | Getting up |

The demonstrated capability is **action interpretation from tiny, appearance-
free body-occupancy sequences without fine-tuning**. Removing appearance did
not remove all useful motion information. It also removed important evidence
about controlled lowering and support; the false fall must not be hidden.

## Aegis relevance and boundaries

- Candidate use: an appearance-minimised motion review that can explain a
  transition and indicate that someone got up. It is not sufficient to release
  or cancel an alert independently.
- SAM still receives source video. This experiment reduces what Muse sees;
  it does not demonstrate local-only processing or biometric anonymity.
- These are dataset labels for acted events, not measured loss of balance,
  clinical distress or intent. Some apparently controlled descents are labelled
  falls, and a sparse mask cannot resolve every such ambiguity.
- One run per condition on eight preselected recordings is not a confidence
  interval, a representative accuracy estimate, or proof of superiority.
- No claim of a world first or of knowledge held by Meta's engineers is made.

## Reproduce and inspect

With the submitted environment, existing licensed corpus files and a Meta key:

```sh
python evals/experiments/motion_capability.py
```

Open `.tmp/motion-capability/index.html` for all RGB/mask inputs and responses.
Exact matching completed requests are cached to avoid repeating paid calls.
The text-only follow-up has its own runner and report:
`evals/experiments/packed_motion.py` and `evals/packed_motion.json`.
