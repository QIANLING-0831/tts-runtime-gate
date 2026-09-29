# tts-runtime-gate

Local-first runtime quality gate for Mandarin TTS. It generates multiple
GPT-SoVITS candidates, rejects technical failures, applies light post-processing,
re-validates the result, and records an auditable manifest.

This is not another MOS benchmark. Existing evaluators such as
[`open-tts-eval`](https://github.com/inworld-ai/open-tts-eval) and
[`TTSProof`](https://github.com/Mormolykos/ttsproof) cover much of the generic
evaluation space. This project focuses on the missing production loop:

- Mandarin display-text to spoken-text normalization;
- local GPT-SoVITS candidate generation across multiple seeds/checkpoints;
- fail-closed ASR and audio-health checks;
- post-processing followed by mandatory re-validation;
- explicit `PASS`, rejection reasons, and provenance in one manifest.

Technical QC does **not** certify naturalness, acting quality, speaker identity,
or absence of electronic timbre. Those require calibrated listening review.

## Quick start

1. Install the dependencies in `requirements.txt` in your GPT-SoVITS Python
   environment.
2. Copy `narration/config/voices/example.yaml` to a file ending in
   `.local.yaml` and fill in local model, reference-audio, and ffmpeg paths.
3. Start the GPT-SoVITS v2 API on `http://127.0.0.1:9880`.
4. Run:

```powershell
python narration/narration_pipeline.py `
  --voice-config narration/config/voices/my-voice.local.yaml `
  run `
  --text "二零二六年，人工智能正在改变教育。" `
  --output-dir narration/runs/demo
```

Audit an existing WAV without generating it:

```powershell
python narration/narration_pipeline.py `
  --voice-config narration/config/voices/my-voice.local.yaml `
  qc sample.wav `
  --expected-text "需要核对的原始文案。"
```

Run the offline tests:

```powershell
python -m unittest discover -s narration/tests -v
```

## Scope and status

The current provider adapter targets GPT-SoVITS. The WAV-level `qc` command is
provider-neutral. A second local provider adapter and competitor execution on a
shared Mandarin corpus are planned before a stable release.

See [the evaluation plan](docs/evaluation/tts-competitor-test-plan.md) and
[the landscape review](docs/research/tts-quality-gate-landscape.md).

## License

MIT
