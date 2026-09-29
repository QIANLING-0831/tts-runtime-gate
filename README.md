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
- a shell-free command provider for other local TTS CLIs;
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

The included providers are GPT-SoVITS and a generic command adapter. The latter
has been exercised end-to-end with the built-in Windows Mandarin SAPI voice;
`narration/config/voices/windows-sapi.example.yaml` is the working example.
CosyVoice, Fish Speech, IndexTTS, or another local engine can be connected by
replacing the argv list, without changing the QC core. The WAV-level `qc`
command remains provider-neutral.

Prepare an anonymous listening review:

```powershell
python narration/blind_review.py prepare `
  --candidate "engine-a=path/to/a.wav" `
  --candidate "engine-b=path/to/b.wav" `
  --output narration/runs/blind-review
```

Fill the generated `scores.json` without opening `private-mapping.json`, then
produce the revealed ranking:

```powershell
python narration/blind_review.py summarize narration/runs/blind-review
```

See [the evaluation plan](docs/evaluation/tts-competitor-test-plan.md) and
[the landscape review](docs/research/tts-quality-gate-landscape.md).

## License

MIT
