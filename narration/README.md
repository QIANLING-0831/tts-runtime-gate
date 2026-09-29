# GPT-SoVITS 自动旁白流水线

这层工具独立于 GPT-SoVITS 核心目录，完成以下工作：

- 将展示文案转换为适合朗读的 provider 文案；
- 固定使用 `cut3` 和短片段间隔；
- 每段生成多个确定性 seed；
- 使用本地 Whisper、静音、削波和音高指标自动筛选；
- 对选中候选执行轻量、确定性的后期处理；
- 输出最终 WAV 与可审计的 `narration_manifest.json`。

## 运行环境

使用安装了 `requirements.txt` 依赖的 Python。若复用 GPT-SoVITS 自带
Python，请将下面的命令替换为其解释器路径：

```powershell
$python = 'python'
```

先启动本地 API：

```powershell
Set-Location '<你的 GPT-SoVITS 目录>'
& $python api_v2.py -a 127.0.0.1 -p 9880 -c '<你的 tts-infer.yaml>'
```

仅检查文本转换和切句，不生成音频：

```powershell
& $python .\narration_pipeline.py prepare --text '2026年，AI正在改变教育。'
```

生成、自动筛选并后期处理：

```powershell
& $python .\narration_pipeline.py run `
  --text '2026年，人工智能正在改变教育。它也带来了新的问题。' `
  --output-dir '.\runs\demo'
```

对现有 WAV 执行同一套技术质检：

```powershell
& $python .\narration_pipeline.py qc '.\sample.wav' `
  --expected-text '需要核对的原始文案。'
```

只执行可迁移的轻量后期，不调用 GPT-SoVITS：

```powershell
& $python .\narration_pipeline.py postprocess '.\sample.wav' '.\sample-clean.wav'
```

生成声音 checkpoint 的盲测样本：

```powershell
& $python .\narration_pipeline.py benchmark `
  --file '.\benchmark\furina-lines.txt' `
  --output-dir '.\runs\checkpoint-benchmark'
```

样本使用 A、B、C、D 匿名命名。先记录主观排序，再查看 `benchmark_manifest.json` 中的 `private_checkpoint_key`，避免 checkpoint 名称影响判断。

该 benchmark 属于实验工具，不能自动改写默认 checkpoint。当前 GPT-SoVITS 的固定 seed 并不保证在 CUDA、进程重启或连续切换权重后得到波形级一致的结果；同一服务中切换多组权重生成的样本必须经过人工盲听，不能只依赖频谱代理分数。

`quality_reference_audio` 用于登记人工认可的黄金听感样本。频谱平坦度、高频能量和频谱跳变目前只记录；在得到足够人工标签前，不参与自动候选决策。

## 迁移到新声音

复制 `config/voices/furina.yaml`，只修改模型权重、参考音频、逐字稿、稳定语速和少量声音专属参数。共享的文本处理、候选筛选和后期配置无需复制。

流水线不会联网下载 ASR 模型。如果本地模型不可用，manifest 会写入降级原因并扣分，但不会伪造 ASR 通过结果。

技术质检通过只代表没有检测到漏字、重复、异常停顿、削波或明显音高问题，不代表情绪表演已经得到人工批准。

## 作为其他本地 TTS 的审核门

`qc` 子命令只接收 WAV 和期望文本，本身不依赖 GPT-SoVITS，可以审核其他本地模型的输出。可迁移的审核项包括 ASR 完整性、异常静音、削波、局部音高尖峰和基本音频统计。

`cut3`、`fragment_interval`、采样参数和权重切换属于 GPT-SoVITS 生成适配，不是通用审核规则。接入其他模型时应替换生成适配器，但保留 `qc` 和后期复检。情绪、角色表现和语义强调仍应由表演审核决定，不能由技术门冒充通过。
