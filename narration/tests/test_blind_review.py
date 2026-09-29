from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "blind_review.py"
SPEC = importlib.util.spec_from_file_location("blind_review", MODULE_PATH)
blind_review = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(blind_review)


class BlindReviewTests(unittest.TestCase):
    def test_prepare_anonymizes_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.wav"
            second = root / "second.wav"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            output = root / "review"
            blind_review.prepare([f"one={first}", f"two={second}"], output, 1)
            mapping = json.loads((output / "private-mapping.json").read_text(encoding="utf-8"))
            self.assertEqual(set(mapping), {"A", "B"})
            self.assertTrue((output / "sample-A.wav").exists())
            self.assertTrue((output / "sample-B.wav").exists())


if __name__ == "__main__":
    unittest.main()
