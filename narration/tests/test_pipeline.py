from __future__ import annotations

import importlib.util
import copy
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf


MODULE_PATH = Path(__file__).resolve().parents[1] / "narration_pipeline.py"
SPEC = importlib.util.spec_from_file_location("narration_pipeline", MODULE_PATH)
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = pipeline.load_config(pipeline.DEFAULT_PIPELINE, pipeline.DEFAULT_VOICE)

    def test_normalizes_year_percent_and_terms(self):
        result = pipeline.normalize_provider_text("2026年，AI增长15.5%。", self.config)
        self.assertEqual(result, "二零二六年，人工智能增长百分之十五点五。")

    def test_split_keeps_complete_sentences(self):
        self.assertEqual(
            pipeline.split_sentences("第一句话。第二句话！第三句？"),
            ["第一句话。", "第二句话！", "第三句？"],
        )

    def test_character_error_rate(self):
        self.assertEqual(pipeline.character_error_rate("你好，世界。", "你好世界"), 0)
        self.assertEqual(pipeline.character_error_rate("改变我们的", "改變我們的"), 0)
        self.assertGreater(pipeline.character_error_rate("你好世界", "你好"), 0)

    def test_short_asr_text_uses_absolute_error_allowance(self):
        metrics = {
            "duration_seconds": 2.0,
            "clip_fraction": 0.0,
            "leading_silence_seconds": 0.0,
            "trailing_silence_seconds": 0.0,
            "max_internal_silence_seconds": 0.0,
            "max_local_pitch_semitones": 0.0,
        }
        result = pipeline.judge_candidate(
            metrics,
            "茶会是淑女的必修课。",
            "茶会是女士必修课",
            None,
            self.config,
        )
        self.assertTrue(result["passed"])
        self.assertLessEqual(result["asr_edit_count"], result["asr_allowed_errors"])

    def test_audio_qc_detects_long_internal_silence(self):
        sample_rate = 16000
        tone = 0.2 * np.sin(2 * np.pi * 220 * np.arange(sample_rate // 2) / sample_rate)
        audio = np.concatenate([tone, np.zeros(sample_rate // 2), tone])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pause.wav"
            sf.write(path, audio, sample_rate)
            metrics = pipeline.analyze_audio(path, "这是一段用于测试的完整中文语音。", self.config)
        self.assertGreater(metrics["max_internal_silence_seconds"], 0.25)

    def test_asr_unavailable_fails_closed(self):
        metrics = {
            "duration_seconds": 2.0,
            "clip_fraction": 0.0,
            "leading_silence_seconds": 0.0,
            "trailing_silence_seconds": 0.0,
            "max_internal_silence_seconds": 0.0,
            "max_local_pitch_semitones": 0.0,
        }
        result = pipeline.judge_candidate(
            metrics,
            "这是一条完整的测试语句。",
            None,
            "local_model_unavailable",
            self.config,
        )
        self.assertFalse(result["passed"])
        self.assertIn("local_model_unavailable", result["rejection_reasons"])

    def test_audio_qc_detects_clipping_and_edge_silence(self):
        sample_rate = 16000
        silence = np.zeros(sample_rate)
        clipped = np.ones(sample_rate // 2)
        audio = np.concatenate([silence, clipped, silence])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clipped.wav"
            sf.write(path, audio, sample_rate)
            metrics = pipeline.analyze_audio(path, "这是一条故障注入测试语句。", self.config)
        result = pipeline.judge_candidate(metrics, "测试", "测试", None, self.config)
        self.assertFalse(result["passed"])
        self.assertIn("clipping", result["rejection_reasons"])
        self.assertIn("edge_silence_too_long", result["rejection_reasons"])

    def test_command_provider_generates_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidate.wav"
            script = (
                "import pathlib,sys; "
                "pathlib.Path(sys.argv[1]).write_bytes(b'RIFFtest')"
            )
            config = {"provider": "command", "command": [sys.executable, "-c", script, "{output}"]}
            pipeline.synthesize_candidate("测试", 7, output, config)
            self.assertEqual(output.read_bytes(), b"RIFFtest")

    def test_artifact_ranking_prefers_reference_like_candidate(self):
        config = copy.deepcopy(self.config)
        config["qc"]["artifact_ranking"]["enabled"] = True
        reference = {
            "spectral_flatness": 0.01,
            "high_frequency_energy_ratio": 0.02,
            "spectral_flux": 0.03,
        }
        candidates = [
            {"score": 100.0, "metrics": dict(reference)},
            {
                "score": 100.0,
                "metrics": {
                    "spectral_flatness": 0.05,
                    "high_frequency_energy_ratio": 0.08,
                    "spectral_flux": 0.09,
                },
            },
        ]
        pipeline.apply_artifact_ranking(candidates, reference, config)
        self.assertEqual(candidates[0]["artifact_penalty"], 0)
        self.assertGreater(candidates[1]["artifact_penalty"], 0)
        self.assertGreater(candidates[0]["score"], candidates[1]["score"])


if __name__ == "__main__":
    unittest.main()
