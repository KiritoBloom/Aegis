# Spectrogram speech separation: negative feasibility result

Run: 2026-11-04. Models: `sam-3.1`, `muse-spark-1.3`,
`muse-voice-transcribe-1.0`. Exact requests, responses, source hashes, script
hash and measurements: [`spectrogram_speech.json`](spectrogram_speech.json).

**This experiment did not demonstrate useful resident-voice isolation.**
It is an exploratory result, not a deployed feature or a field benchmark.

## Material and method

- Downloaded a real 14.225-second LibriSpeech utterance, `6930-75918-0001`,
  with its published 43-word transcript from the Hugging Face dataset viewer.
- Downloaded a different, female speaker from Google FLEURS, English test
  utterance `18066939864018796519`, distributed as a WAV fixture by
  `podscripter-project/test-fixtures`.
- Repeated/cropped and RMS-scaled the interfering speaker to be 6 dB or 12 dB
  louder during overlap. The target speaks alone for the first two seconds,
  giving the models a favorable target-voice cue. The rest overlaps.
- This is a **controlled TV-speech proxy**, not an actual loud television,
  reverberant home, call for help, or test with a resident.
- Converted the mixture to a 0–4 kHz spectrogram. Muse inspected it and
  proposed a SAM visual prompt. SAM also received two fixed generic prompts.
  Muse then reviewed the resulting masks and selected any target-specific ones.
- Reconstructed audio using the predicted masks and original mixture phase.
  No expected words or overlapping clean source were supplied to the separation
  models or transcription model. Muse also proposed direct time/frequency
  regions in one condition; those were evaluated separately.
- Compared against raw audio, an ordinary 300–3400 Hz bandpass, and an
  **evaluation-only oracle mask** computed from both known clean sources.
  The oracle is not available in real deployment.

## Results

Target word error rate; lower is better. One transcription per signal.

| Input to Muse Transcribe | Interferer +6 dB | Interferer +12 dB |
|---|---:|---:|
| Unprocessed mixture | **0.0%** | **25.6%** |
| Ordinary bandpass | 88.4% | 86.1% |
| Forced union of SAM masks, including rejected masks | 9.3% | 86.1% |
| Muse-selected SAM masks | No usable masks; abstained | No usable masks; abstained |
| Muse's direct spectral regions | None proposed | 86.1% |
| Oracle mask using clean ground truth | 2.3% | **0.0%** |

Clean target transcription had 2.3% WER (one repeated word). Small differences
must not be treated as statistically meaningful with one call per condition.

Both generic SAM prompts returned no objects. Each Muse-directed prompt
returned one mask covering almost the entire spectrogram. Muse correctly
rejected both as containing both speakers. Forcing these masks through the
audio reconstruction worsened transcription rather than isolating the target.

Signal measurements agree with that failure: target SI-SDR during overlap was
-6.10 / -12.21 dB in the mixtures and -7.81 / -13.71 dB after forced SAM masking.
The oracle reached +14.35 / +11.98 dB. The unmasked STFT roundtrip reproduced
the source within 7e-16 maximum absolute error, checking the reconstruction
code independently of model performance.

## Conclusion

The earlier synthetic-impact experiment showed that SAM can locate simple
vertical structures in a sound image. That did **not** transfer to identifying
one person's overlapping speech. The missing capability here is source-specific
mask prediction; reconstructing audio from a correct mask is feasible.

This evidence does not support shipping or pitching SAM-image-based resident
speech isolation. It also does not prove that every representation or future
model would fail; two mixtures from one speaker pair are a feasibility check.

## Reproduce and listen

```bash
python evals/experiments/spectrogram_speech.py
```

Uses the submitted Python environment, ffmpeg and `META_API_KEY`. Matching
completed requests are cached; deleting `.tmp/spectrogram-speech` performs a
fresh download and fresh paid model calls. All results, including failures,
are retained. Audio and the playback comparison page are local:

`.tmp/spectrogram-speech/listen.html`

## Source credit

- [LibriSpeech / OpenSLR](https://www.openslr.org/12): V. Panayotov, G. Chen,
  D. Povey and S. Khudanpur, *LibriSpeech: An ASR corpus based on public domain
  audio books*, ICASSP 2015; CC-BY 4.0.
- [Google FLEURS](https://huggingface.co/datasets/google/fleurs): A. Conneau
  et al., *FLEURS: Few-shot Learning Evaluation of Universal Representations
  of Speech*, 2022; CC-BY 4.0. WAV conversion distributed by
  [podscripter-project/test-fixtures](https://huggingface.co/datasets/podscripter-project/test-fixtures).
- Modifications: mono/16 kHz decoding, repeated/cropped interference, mixing,
  level adjustment, spectrogram masking and reconstruction. Original media is
  not included in the repository; source URLs and hashes are in the JSON report.
