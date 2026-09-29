# tts-runtime-gate

Local-first runtime quality gate for Mandarin TTS. It generates multiple
GPT-SoVITS candidates, rejects technical failures, applies light post-processing,
re-validates the result, and records an auditable manifest.

## 这是做什么的

`tts-runtime-gate` 不是一个新的 TTS 模型，而是放在本地 TTS 模型与视频制作
之间的生产质量控制层。它负责把中文文案转换为朗读文本，生成多个候选，自动
淘汰漏字、异常停顿、削波等技术故障，对入选音频做轻处理并再次审核，最后输出
带有模型参数、候选结果和选择理由的 manifest。

它适合本地旁白、批量视频配音以及需要保留生成记录的工作流。GPT-SoVITS 是
首个完整适配器；其他本地 TTS 可以通过命令适配器接入同一套 WAV 审核流程。

## 与同类项目相比

[`open-tts-eval`](https://github.com/inworld-ai/open-tts-eval) 和
[`TTSProof`](https://github.com/Mormolykos/ttsproof) 在通用指标、结构故障、报表
和回归测试方面更成熟，本项目不重复追求这些能力的数量。它的优势在于：

- **中文优先**：分开展示文案与实际朗读文案，处理年份、百分比、中英混读和
  中文语境；实测 `open-tts-eval 0.1.0` 的默认英文归一化链路不能直接审核中文。
- **生成时闭环**：不只给已有 WAV 打分，还负责多 seed/checkpoint 候选生成、
  淘汰、选择、轻处理以及处理后的再次审核。
- **本地模型优先**：GPT-SoVITS 可直接切换本地权重；通用命令适配器允许接入
  CosyVoice、Fish Speech、IndexTTS 或其他本地 CLI。
- **失败关闭**：ASR 或关键测量不可用时不会假装通过，而是拒绝或要求复核。
- **人工听感有明确位置**：自动门只判断技术缺陷；真人感、电音感和表演质量
  使用匿名盲听与每个声音独立的黄金参考样本，不让 MOS 或频谱代理冒充耳朵。
- **可审计交付**：从原文、朗读文本、模型参数、候选、后处理到最终文件均写入
  manifest，适合真正的旁白生产过程，而不只是离线 benchmark。

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

黄金听感样本按声音单独配置。它只作为人工认可基线，不参与硬性自动评分，且
默认不提交到公开仓库，以避免上传受许可限制的声音素材或本机路径。

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
