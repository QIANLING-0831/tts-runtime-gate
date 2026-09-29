"""Reusable GPT-SoVITS Mandarin narration runner with deterministic QC.

The runner deliberately keeps voice-specific details in YAML. It does not
change the GPT-SoVITS installation and it never downloads an ASR model.
"""

from __future__ import annotations

import argparse
import copy
import functools
import json
import math
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parent
DEFAULT_PIPELINE = ROOT / "config" / "pipeline.yaml"
DEFAULT_VOICE = ROOT / "config" / "voices" / "example.yaml"
DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")
FULLWIDTH_TO_ASCII = str.maketrans("０１２３４５６７８９％", "0123456789%")
SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?", re.MULTILINE)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return payload


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(pipeline_path: Path, voice_path: Path) -> dict[str, Any]:
    return deep_merge(load_yaml(pipeline_path), load_yaml(voice_path))


def _number_to_chinese(value: str) -> str:
    try:
        import cn2an

        return cn2an.an2cn(value, "low")
    except Exception:
        return value.translate(DIGITS).replace(".", "点")


def normalize_provider_text(text: str, config: dict[str, Any]) -> str:
    """Create spoken copy without changing the display copy."""
    # Do not normalize the entire string with NFKC: that also changes Chinese
    # punctuation such as `，` into ASCII punctuation and alters prosody.
    normalized = text.translate(FULLWIDTH_TO_ASCII).strip()

    def year(match: re.Match[str]) -> str:
        return match.group(1).translate(DIGITS) + "年"

    def percent(match: re.Match[str]) -> str:
        return "百分之" + _number_to_chinese(match.group(1))

    normalized = re.sub(r"(?<!\d)(\d{4})年", year, normalized)
    normalized = re.sub(r"(?<!\d)(\d+(?:\.\d+)?)%", percent, normalized)

    replacements = config.get("text", {}).get("replacements", {})
    for source in sorted(replacements, key=len, reverse=True):
        normalized = normalized.replace(source, str(replacements[source]))

    normalized = re.sub(r"[ \t]+", "", normalized)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized


def split_sentences(text: str) -> list[str]:
    return [match.group(0).strip() for match in SENTENCE_RE.finditer(text) if match.group(0).strip()]


def lint_provider_text(text: str, config: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    minimum = int(config.get("text", {}).get("min_fragment_chars", 8))
    for segment in split_sentences(text):
        visible = re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", segment)
        if visible and len(visible) < minimum:
            issues.append({
                "type": "short_fragment",
                "text": segment,
                "message": f"片段少于 {minimum} 个有效字符，建议与相邻语义句合并",
            })

    unstable = config.get("text", {}).get("unstable_terms", {})
    for term, hint in unstable.items():
        if re.search(re.escape(term) + r"[，、。！？；]", text):
            issues.append({"type": "unstable_term", "text": term, "message": str(hint)})
    return issues


@functools.lru_cache(maxsize=1)
def _opencc_converter():
    try:
        from opencc import OpenCC

        return OpenCC("t2s")
    except Exception:
        return None


def compact_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    converter = _opencc_converter()
    if converter is not None:
        text = converter.convert(text)
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", text)


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def character_error_rate(expected: str, actual: str) -> float:
    expected_compact = compact_text(expected)
    actual_compact = compact_text(actual)
    if not expected_compact:
        return 0.0 if not actual_compact else 1.0
    return edit_distance(expected_compact, actual_compact) / len(expected_compact)


def _longest_true_run(mask: Any, frame_seconds: float) -> float:
    longest = current = 0
    for value in mask:
        current = current + 1 if bool(value) else 0
        longest = max(longest, current)
    return longest * frame_seconds


def analyze_audio(path: Path, expected_text: str, config: dict[str, Any]) -> dict[str, Any]:
    import librosa
    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(path, always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = np.mean(audio, axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    duration = len(audio) / sample_rate if sample_rate else 0.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    clip_fraction = float(np.mean(np.abs(audio) >= 0.999)) if len(audio) else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0

    frame_length = max(256, int(sample_rate * 0.02))
    hop_length = frame_length
    framed_rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length, center=False)[0]
    rms_db = librosa.amplitude_to_db(framed_rms + 1e-10, ref=1.0)
    threshold = float(config["qc"].get("silence_threshold_db", -45))
    voiced_indices = np.flatnonzero(rms_db > threshold)
    internal_silence = 0.0
    leading_silence = duration
    trailing_silence = duration
    if len(voiced_indices) > 1:
        leading_silence = voiced_indices[0] * hop_length / sample_rate
        trailing_silence = max(0.0, duration - ((voiced_indices[-1] + 1) * hop_length / sample_rate))
        internal = rms_db[voiced_indices[0] : voiced_indices[-1] + 1] <= threshold
        internal_silence = _longest_true_run(internal, hop_length / sample_rate)

    voiced_audio = audio
    if len(voiced_indices) > 1:
        start_sample = max(0, int(voiced_indices[0] * hop_length))
        end_sample = min(len(audio), int((voiced_indices[-1] + 1) * hop_length))
        voiced_audio = audio[start_sample:end_sample]

    spectral_flatness = high_frequency_ratio = spectral_flux = 0.0
    if len(voiced_audio) >= 1024:
        spectrum = np.abs(librosa.stft(voiced_audio, n_fft=1024, hop_length=256))
        power = np.square(spectrum)
        spectral_flatness = float(np.mean(librosa.feature.spectral_flatness(S=power)))
        frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=1024)
        total_energy = float(np.sum(power)) + 1e-12
        high_frequency_ratio = float(np.sum(power[frequencies >= 6500])) / total_energy
        normalized = spectrum / (np.sum(spectrum, axis=0, keepdims=True) + 1e-12)
        if normalized.shape[1] > 1:
            spectral_flux = float(np.mean(np.sqrt(np.sum(np.diff(normalized, axis=1) ** 2, axis=0))))

    f0, _, _ = librosa.pyin(
        audio,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sample_rate,
        frame_length=1024,
        hop_length=max(1, int(sample_rate * 0.01)),
    )
    voiced_f0 = f0[np.isfinite(f0)]
    pitch_spike = 0.0
    if len(voiced_f0) >= 10:
        baseline = float(np.median(voiced_f0))
        window = max(3, int(0.35 / 0.01))
        local_p90 = []
        finite = np.isfinite(f0)
        for start in range(0, len(f0), max(1, window // 2)):
            values = f0[start : start + window][finite[start : start + window]]
            if len(values) >= 3:
                local_p90.append(float(np.percentile(values, 90)))
        if local_p90 and baseline > 0:
            pitch_spike = max(0.0, 12 * math.log2(max(local_p90) / baseline))

    visible_chars = len(compact_text(expected_text))
    metrics: dict[str, Any] = {
        "duration_seconds": round(duration, 3),
        "sample_rate": int(sample_rate),
        "peak_dbfs": round(20 * math.log10(max(peak, 1e-10)), 2),
        "rms_dbfs": round(20 * math.log10(max(rms, 1e-10)), 2),
        "clip_fraction": round(clip_fraction, 7),
        "leading_silence_seconds": round(leading_silence, 3),
        "trailing_silence_seconds": round(trailing_silence, 3),
        "max_internal_silence_seconds": round(internal_silence, 3),
        "max_local_pitch_semitones": round(pitch_spike, 2),
        "spectral_flatness": round(spectral_flatness, 7),
        "high_frequency_energy_ratio": round(high_frequency_ratio, 7),
        "spectral_flux": round(spectral_flux, 7),
        "characters_per_second": round(visible_chars / duration, 2) if duration else 0.0,
    }
    return metrics


class LocalASR:
    def __init__(self, config: dict[str, Any]):
        self.enabled = bool(config["qc"].get("asr_enabled", True))
        self.model = None
        self.reason: str | None = None
        if not self.enabled:
            self.reason = "disabled_by_config"
            return
        try:
            from faster_whisper import WhisperModel

            self.model = WhisperModel(
                config["qc"].get("asr_model", "base"),
                device=config["qc"].get("asr_device", "cpu"),
                compute_type=config["qc"].get("asr_compute_type", "int8"),
                local_files_only=True,
            )
        except Exception as exc:
            self.reason = f"local_model_unavailable: {exc}"

    def transcribe(self, path: Path) -> tuple[str | None, str | None]:
        if self.model is None:
            return None, self.reason
        segments, _ = self.model.transcribe(str(path), language="zh", beam_size=5, vad_filter=True)
        return "".join(segment.text for segment in segments).strip(), None


def judge_candidate(
    metrics: dict[str, Any],
    expected_text: str,
    transcript: str | None,
    asr_reason: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    qc = config["qc"]
    reasons: list[str] = []
    warnings: list[str] = []
    score = 100.0

    if metrics["duration_seconds"] < float(qc.get("min_duration_seconds", 0.35)):
        reasons.append("audio_too_short")
    if metrics["clip_fraction"] > float(qc.get("max_clip_fraction", 0.001)):
        reasons.append("clipping")
    if metrics["max_internal_silence_seconds"] > float(qc.get("max_internal_silence_seconds", 0.25)):
        reasons.append("internal_pause_too_long")
        score -= 25
    edge_limit = float(qc.get("max_edge_silence_seconds", 0.40))
    if max(metrics["leading_silence_seconds"], metrics["trailing_silence_seconds"]) > edge_limit:
        if qc.get("fail_on_edge_silence", True):
            reasons.append("edge_silence_too_long")
        else:
            warnings.append("fixable_edge_silence")
        score -= 15
    pitch_limit = float(qc.get("max_local_pitch_semitones", 4.0))
    if metrics["max_local_pitch_semitones"] > pitch_limit:
        if qc.get("fail_on_pitch_spike", True):
            reasons.append("unexplained_pitch_spike")
        else:
            warnings.append("possible_pitch_spike")
        score -= min(30, (metrics["max_local_pitch_semitones"] - pitch_limit) * 5)

    cer = None
    asr_edit_count = None
    asr_allowed_errors = None
    if transcript is not None:
        expected_compact = compact_text(expected_text)
        actual_compact = compact_text(transcript)
        asr_edit_count = edit_distance(expected_compact, actual_compact)
        cer = asr_edit_count / max(1, len(expected_compact))
        proportional_allowance = math.ceil(len(expected_compact) * float(qc.get("max_cer", 0.12)))
        if len(expected_compact) <= int(qc.get("short_text_chars", 12)):
            asr_allowed_errors = max(
                proportional_allowance,
                int(qc.get("short_text_allowed_asr_errors", 3)),
            )
        else:
            asr_allowed_errors = max(
                proportional_allowance,
                int(qc.get("long_text_min_allowed_asr_errors", 2)),
            )
        if asr_edit_count > asr_allowed_errors:
            reasons.append("asr_mismatch")
        score -= min(50, cer * 100)
    else:
        asr_failure = asr_reason or "asr_not_run"
        if qc.get("fail_on_asr_unavailable", True):
            reasons.append(asr_failure)
        else:
            warnings.append(asr_failure)
        score -= 5

    score -= min(10, metrics["clip_fraction"] * 10000)
    return {
        "passed": not reasons,
        "score": round(max(0.0, score), 2),
        "rejection_reasons": reasons,
        "warnings": warnings,
        "asr_transcript": transcript,
        "cer": round(cer, 4) if cer is not None else None,
        "asr_edit_count": asr_edit_count,
        "asr_allowed_errors": asr_allowed_errors,
    }


def apply_artifact_ranking(
    candidates: list[dict[str, Any]],
    reference_metrics: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Soft-rank spectral artifacts by distance from the real reference.

    This never rejects a candidate. The meaning of a globally high or low
    spectral value is voice-dependent, so proximity to the selected voice's
    reference is safer than a universal threshold.
    """
    ranking = config.get("qc", {}).get("artifact_ranking", {})
    if not ranking.get("enabled", True):
        return
    weights = ranking.get("weights", {})
    max_penalty = float(ranking.get("max_penalty", 8.0))
    for candidate in candidates:
        weighted_delta = 0.0
        total_weight = 0.0
        deltas: dict[str, float] = {}
        for metric, weight_value in weights.items():
            weight = float(weight_value)
            observed = float(candidate["metrics"].get(metric, 0.0))
            reference = float(reference_metrics.get(metric, 0.0))
            if observed <= 0 or reference <= 0 or weight <= 0:
                continue
            delta = min(2.0, abs(math.log((observed + 1e-12) / (reference + 1e-12))))
            deltas[metric] = round(delta, 4)
            weighted_delta += delta * weight
            total_weight += weight
        normalized_delta = weighted_delta / total_weight if total_weight else 0.0
        penalty = min(max_penalty, normalized_delta * max_penalty / 2.0)
        candidate["artifact_distance"] = deltas
        candidate["artifact_penalty"] = round(penalty, 2)
        candidate["score"] = round(max(0.0, float(candidate["score"]) - penalty), 2)


def api_request(method: str, url: str, *, timeout: int, **kwargs: Any):
    import requests

    response = requests.request(method, url, timeout=timeout, **kwargs)
    if not response.ok:
        raise RuntimeError(f"GPT-SoVITS API {response.status_code}: {response.text[:1000]}")
    return response


def configure_weights(config: dict[str, Any]) -> None:
    if config.get("provider", "gpt-sovits") != "gpt-sovits":
        return
    base = config["api_url"].rstrip("/")
    timeout = int(config.get("request_timeout_seconds", 300))
    api_request("GET", f"{base}/set_gpt_weights", timeout=timeout, params={"weights_path": config["gpt_weight"]})
    api_request("GET", f"{base}/set_sovits_weights", timeout=timeout, params={"weights_path": config["sovits_weight"]})


def synthesize_candidate(text: str, seed: int, output: Path, config: dict[str, Any]) -> None:
    if config.get("provider", "gpt-sovits") == "command":
        command = config.get("command")
        if not isinstance(command, list) or not command:
            raise ValueError("command provider requires a non-empty command list")
        output.parent.mkdir(parents=True, exist_ok=True)
        values = {"text": text, "output": str(output.resolve()), "seed": str(seed)}
        rendered = [str(part).format_map(values) for part in command]
        completed = subprocess.run(rendered, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(f"command provider failed: {completed.stderr[-1000:]}")
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("command provider did not create a WAV file")
        return

    inference = config["inference"]
    payload = {
        "text": text,
        "text_lang": config.get("language", "zh"),
        "ref_audio_path": config["reference_audio"],
        "prompt_text": config["reference_text"],
        "prompt_lang": config.get("language", "zh"),
        "top_k": int(inference["top_k"]),
        "top_p": float(inference["top_p"]),
        "temperature": float(inference["temperature"]),
        "text_split_method": inference.get("text_split_method", "cut3"),
        "batch_size": int(inference.get("batch_size", 1)),
        "speed_factor": float(inference.get("speed_factor", 1.0)),
        "fragment_interval": float(inference.get("fragment_interval", 0.15)),
        "seed": int(seed),
        "parallel_infer": True,
        "repetition_penalty": float(inference.get("repetition_penalty", 1.25)),
        "streaming_mode": 0,
        "media_type": "wav",
    }
    response = api_request(
        "POST",
        config["api_url"].rstrip("/") + "/tts",
        timeout=int(config.get("request_timeout_seconds", 300)),
        json=payload,
    )
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        raise RuntimeError(response.text)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)


def _run_ffmpeg_filters(
    source: Path,
    target: Path,
    filters: list[str],
    sample_rate: int,
    config: dict[str, Any],
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(config["ffmpeg"]), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
    ]
    if filters:
        command.extend(["-af", ",".join(filters)])
    command.extend(["-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(target)])
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(f"ffmpeg postprocess failed: {completed.stderr}")


def clean_section_audio(source: Path, target: Path, config: dict[str, Any]) -> None:
    import librosa
    import numpy as np
    import soundfile as sf

    post = config["postprocess"]
    audio, sample_rate = sf.read(source, always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = np.mean(audio, axis=1)
    trimmed, _ = librosa.effects.trim(audio, top_db=float(post.get("trim_top_db", 45)))
    padding = np.zeros(int(sample_rate * float(post.get("edge_padding_seconds", 0.08))))
    prepared = np.concatenate([padding, trimmed, padding])

    with tempfile.TemporaryDirectory(prefix="narration-") as directory:
        intermediate = Path(directory) / "trimmed.wav"
        sf.write(intermediate, prepared, sample_rate, subtype="PCM_16")
        filters = [f"highpass=f={int(post.get('highpass_hz', 65))}"]
        _run_ffmpeg_filters(intermediate, target, filters, sample_rate, config)


def master_audio(source: Path, target: Path, config: dict[str, Any]) -> None:
    import soundfile as sf

    post = config["postprocess"]
    sample_rate = sf.info(source).samplerate
    filters: list[str] = []
    if post.get("compressor_enabled", False):
        filters.append(
            "acompressor="
            f"threshold={float(post.get('compressor_threshold_db', -18))}dB:"
            f"ratio={float(post.get('compressor_ratio', 2.0))}:"
            f"attack={float(post.get('compressor_attack_ms', 20))}:"
            f"release={float(post.get('compressor_release_ms', 150))}"
        )
    filters.append(
            "loudnorm="
            f"I={float(post.get('target_lufs', -20))}:"
            f"LRA={float(post.get('loudness_range', 7))}:"
            f"TP={float(post.get('true_peak_db', -1.0))}"
    )
    _run_ffmpeg_filters(source, target, filters, sample_rate, config)


def postprocess_audio(source: Path, target: Path, config: dict[str, Any]) -> None:
    """Standalone trim/high-pass followed by one final master pass."""
    with tempfile.TemporaryDirectory(prefix="narration-stage-") as directory:
        staged = Path(directory) / "staged.wav"
        clean_section_audio(source, staged, config)
        master_audio(staged, target, config)


def join_audio(files: list[Path], segments: list[str], target: Path, config: dict[str, Any]) -> float:
    import numpy as np
    import soundfile as sf

    arrays = []
    sample_rate = None
    post = config["postprocess"]
    for index, path in enumerate(files):
        audio, current_rate = sf.read(path, always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = np.mean(audio, axis=1)
        if sample_rate is None:
            sample_rate = current_rate
        elif current_rate != sample_rate:
            raise ValueError("Candidate sample rates do not match")
        arrays.append(np.asarray(audio, dtype=np.float32))
        if index < len(files) - 1:
            gap = post.get("question_gap_seconds", 0.28) if segments[index].endswith(("?", "？", "!", "！")) else post.get("sentence_gap_seconds", 0.24)
            arrays.append(np.zeros(int(sample_rate * float(gap)), dtype=np.float32))
    combined = np.concatenate(arrays) if arrays else np.zeros(0, dtype=np.float32)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, combined, sample_rate or 32000, subtype="PCM_16")
    return len(combined) / (sample_rate or 32000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_payload(display_text: str, config: dict[str, Any]) -> dict[str, Any]:
    provider_text = normalize_provider_text(display_text, config)
    return {
        "display_text": display_text,
        "provider_text": provider_text,
        "segments": split_sentences(provider_text),
        "lint": lint_provider_text(provider_text, config),
    }


def run_pipeline(display_text: str, output_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    prepared = prepare_payload(display_text, config)
    if not prepared["segments"]:
        raise ValueError("No speakable text was found")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    clean_dir = output_dir / "clean"

    configure_weights(config)
    asr = LocalASR(config)
    reference_metrics = {}
    reference_audio = config.get("quality_reference_audio") or config.get("reference_audio")
    if reference_audio and Path(reference_audio).is_file():
        reference_metrics = analyze_audio(
            Path(reference_audio), config.get("reference_text", ""), config
        )
    raw_candidate_config = copy.deepcopy(config)
    raw_candidate_config["qc"]["fail_on_edge_silence"] = False
    selected_files: list[Path] = []
    section_reports = []
    all_passed = True

    for section_index, segment in enumerate(prepared["segments"], 1):
        candidate_reports = []
        for seed in config["inference"]["seeds"]:
            raw_path = raw_dir / f"section-{section_index:03d}-seed-{seed}.wav"
            synthesize_candidate(segment, int(seed), raw_path, config)
            metrics = analyze_audio(raw_path, segment, config)
            transcript, asr_reason = asr.transcribe(raw_path)
            judgment = judge_candidate(
                metrics, segment, transcript, asr_reason, raw_candidate_config
            )
            candidate_reports.append({
                "seed": int(seed),
                "raw_file": str(raw_path.resolve()),
                "metrics": metrics,
                **judgment,
            })

        apply_artifact_ranking(candidate_reports, reference_metrics, config)
        passing = [item for item in candidate_reports if item["passed"]]
        pool = passing or candidate_reports
        selected = max(pool, key=lambda item: item["score"])
        section_passed = bool(passing)
        all_passed = all_passed and section_passed
        clean_path = clean_dir / f"section-{section_index:03d}.wav"
        clean_section_audio(Path(selected["raw_file"]), clean_path, config)
        clean_metrics = analyze_audio(clean_path, segment, config)
        clean_transcript, clean_asr_reason = asr.transcribe(clean_path)
        clean_judgment = judge_candidate(
            clean_metrics, segment, clean_transcript, clean_asr_reason, config
        )
        section_passed = section_passed and clean_judgment["passed"]
        all_passed = all_passed and clean_judgment["passed"]
        selected_files.append(clean_path)
        section_reports.append({
            "section": section_index,
            "provider_text": segment,
            "technical_qc": "PASS" if section_passed else "REVIEW_REQUIRED",
            "selected_seed": selected["seed"],
            "selected_file": str(clean_path.resolve()),
            "postprocess_qc": {"metrics": clean_metrics, **clean_judgment},
            "candidates": candidate_reports,
        })

    pre_master_path = output_dir / "narration-pre-master.wav"
    join_audio(selected_files, prepared["segments"], pre_master_path, config)
    final_path = output_dir / "narration.wav"
    master_audio(pre_master_path, final_path, config)
    final_metrics = analyze_audio(final_path, prepared["provider_text"], config)
    final_transcript, final_asr_reason = asr.transcribe(final_path)
    final_config = copy.deepcopy(config)
    final_config["qc"]["max_internal_silence_seconds"] = max(
        float(config["qc"].get("max_internal_silence_seconds", 0.25)),
        float(config["postprocess"].get("question_gap_seconds", 0.28)) + 0.08,
    )
    final_judgment = judge_candidate(
        final_metrics,
        prepared["provider_text"],
        final_transcript,
        final_asr_reason,
        final_config,
    )
    all_passed = all_passed and final_judgment["passed"]
    duration = final_metrics["duration_seconds"]
    if config.get("provider", "gpt-sovits") == "gpt-sovits":
        execution = {
            "reference_audio": config["reference_audio"],
            "reference_text": config["reference_text"],
            "gpt_weight": config["gpt_weight"],
            "sovits_weight": config["sovits_weight"],
            **config["inference"],
        }
    else:
        execution = {"command": config["command"], **config["inference"]}

    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "provider": config.get("provider", "gpt-sovits"),
        "voice": config.get("name", "unknown"),
        "display_text": prepared["display_text"],
        "provider_text": prepared["provider_text"],
        "text_lint": prepared["lint"],
        "execution": execution,
        "sections": section_reports,
        "final_audio_qc": {"metrics": final_metrics, **final_judgment},
        "pre_master_file": str(pre_master_path.resolve()),
        "selected_file": str(final_path.resolve()),
        "actual_duration_seconds": round(duration, 3),
        "gates": {"technical_qc_passed": all_passed},
    }
    manifest_path = output_dir / "narration_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def run_checkpoint_benchmark(
    display_text: str,
    output_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    benchmark = config.get("benchmark", {})
    gpt_weights = benchmark.get("gpt_weights", {})
    sovits_weights = benchmark.get("sovits_weights", {})
    if not gpt_weights or not sovits_weights:
        raise ValueError("Voice config must define benchmark.gpt_weights and benchmark.sovits_weights")

    prepared = prepare_payload(display_text, config)
    provider_text = prepared["provider_text"]
    segments = prepared["segments"]
    seed = int(benchmark.get("seed", config["inference"]["seeds"][0]))
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    staged_dir = output_dir / "staged"
    sample_dir = output_dir / "samples"
    asr = LocalASR(config)
    reference_metrics = analyze_audio(
        Path(config["reference_audio"]), config["reference_text"], config
    )
    reports: list[dict[str, Any]] = []
    labels = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    try:
        for gpt_name, gpt_path in gpt_weights.items():
            for sovits_name, sovits_path in sovits_weights.items():
                label = next(labels)
                variant = copy.deepcopy(config)
                variant["gpt_weight"] = gpt_path
                variant["sovits_weight"] = sovits_path
                configure_weights(variant)
                raw_variant_config = copy.deepcopy(variant)
                raw_variant_config["qc"]["fail_on_edge_silence"] = False

                staged_files: list[Path] = []
                section_reports: list[dict[str, Any]] = []
                for section_index, segment in enumerate(segments, 1):
                    section_seed = seed + section_index - 1
                    raw_path = raw_dir / f"sample-{label}-section-{section_index:03d}.wav"
                    staged_path = staged_dir / f"sample-{label}-section-{section_index:03d}.wav"
                    synthesize_candidate(segment, section_seed, raw_path, variant)
                    raw_metrics = analyze_audio(raw_path, segment, variant)
                    raw_transcript, raw_reason = asr.transcribe(raw_path)
                    raw_judgment = judge_candidate(
                        raw_metrics,
                        segment,
                        raw_transcript,
                        raw_reason,
                        raw_variant_config,
                    )
                    clean_section_audio(raw_path, staged_path, variant)
                    staged_metrics = analyze_audio(staged_path, segment, variant)
                    staged_transcript, staged_reason = asr.transcribe(staged_path)
                    staged_judgment = judge_candidate(
                        staged_metrics,
                        segment,
                        staged_transcript,
                        staged_reason,
                        variant,
                    )
                    staged_files.append(staged_path)
                    section_reports.append({
                        "section": section_index,
                        "text": segment,
                        "seed": section_seed,
                        "raw_file": str(raw_path.resolve()),
                        "raw_qc": {"metrics": raw_metrics, **raw_judgment},
                        "staged_file": str(staged_path.resolve()),
                        "staged_qc": {"metrics": staged_metrics, **staged_judgment},
                    })

                pre_master_path = output_dir / "pre-master" / f"sample-{label}.wav"
                sample_path = sample_dir / f"sample-{label}.wav"
                join_audio(staged_files, segments, pre_master_path, variant)
                master_audio(pre_master_path, sample_path, variant)
                final_metrics = analyze_audio(sample_path, provider_text, variant)
                final_transcript, final_reason = asr.transcribe(sample_path)
                final_variant = copy.deepcopy(variant)
                final_variant["qc"]["max_internal_silence_seconds"] = max(
                    float(variant["qc"].get("max_internal_silence_seconds", 0.25)),
                    float(variant["postprocess"].get("question_gap_seconds", 0.28)) + 0.08,
                )
                final_judgment = judge_candidate(
                    final_metrics,
                    provider_text,
                    final_transcript,
                    final_reason,
                    final_variant,
                )

                mean_metrics = {}
                for metric in ("spectral_flatness", "high_frequency_energy_ratio", "spectral_flux"):
                    values = [float(item["raw_qc"]["metrics"][metric]) for item in section_reports]
                    mean_metrics[metric] = sum(values) / len(values)
                mean_score = sum(float(item["raw_qc"]["score"]) for item in section_reports) / len(section_reports)
                report = {
                    "label": label,
                    "gpt_checkpoint": gpt_name,
                    "gpt_weight": gpt_path,
                    "sovits_checkpoint": sovits_name,
                    "sovits_weight": sovits_path,
                    "seed": seed,
                    "sections": section_reports,
                    "pre_master_file": str(pre_master_path.resolve()),
                    "sample_file": str(sample_path.resolve()),
                    "final_qc": {"metrics": final_metrics, **final_judgment},
                    "metrics": mean_metrics,
                    "score": round(mean_score, 2),
                }
                reports.append(report)

        apply_artifact_ranking(reports, reference_metrics, config)
    finally:
        configure_weights(config)

    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "provider": "gpt-sovits",
        "voice": config.get("name", "unknown"),
        "display_text": display_text,
        "provider_text": provider_text,
        "reference_audio": config["reference_audio"],
        "seed": seed,
        "blind_samples": [
            {
                "label": report["label"],
                "sample_file": report["sample_file"],
                "technical_qc_passed": (
                    all(
                        item["raw_qc"]["passed"] and item["staged_qc"]["passed"]
                        for item in report["sections"]
                    )
                    and report["final_qc"]["passed"]
                ),
                "score": report["score"],
            }
            for report in reports
        ],
        "private_checkpoint_key": reports,
    }
    manifest_path = output_dir / "benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def read_input(args: argparse.Namespace) -> str:
    if bool(args.text) == bool(args.file):
        raise ValueError("Provide exactly one of --text or --file")
    return args.text if args.text else Path(args.file).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GPT-SoVITS narration generation and automatic QC")
    parser.add_argument("--config", type=Path, default=DEFAULT_PIPELINE)
    parser.add_argument("--voice-config", type=Path, default=DEFAULT_VOICE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("prepare", "run", "benchmark"):
        command = subparsers.add_parser(name)
        command.add_argument("--text")
        command.add_argument("--file", type=Path)
        if name in ("run", "benchmark"):
            command.add_argument("--output-dir", type=Path, required=True)

    qc = subparsers.add_parser("qc")
    qc.add_argument("audio", type=Path)
    qc.add_argument("--expected-text", required=True)
    postprocess = subparsers.add_parser("postprocess")
    postprocess.add_argument("audio", type=Path)
    postprocess.add_argument("output", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config, args.voice_config)
    try:
        if args.command == "prepare":
            print(json.dumps(prepare_payload(read_input(args), config), ensure_ascii=False, indent=2))
            return 0
        if args.command == "qc":
            metrics = analyze_audio(args.audio, args.expected_text, config)
            asr = LocalASR(config)
            transcript, reason = asr.transcribe(args.audio)
            report = {"metrics": metrics, **judge_candidate(metrics, args.expected_text, transcript, reason, config)}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 2
        if args.command == "postprocess":
            postprocess_audio(args.audio, args.output, config)
            print(json.dumps({"output": str(args.output.resolve())}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "benchmark":
            manifest = run_checkpoint_benchmark(read_input(args), args.output_dir, config)
            print(json.dumps({"samples": manifest["blind_samples"]}, ensure_ascii=False, indent=2))
            return 0 if all(item["technical_qc_passed"] for item in manifest["blind_samples"]) else 2

        manifest = run_pipeline(read_input(args), args.output_dir, config)
        print(json.dumps({
            "selected_file": manifest["selected_file"],
            "technical_qc_passed": manifest["gates"]["technical_qc_passed"],
        }, ensure_ascii=False, indent=2))
        return 0 if manifest["gates"]["technical_qc_passed"] else 2
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
