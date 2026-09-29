"""Prepare and summarize a small anonymous listening review."""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path


def prepare(candidates: list[str], output: Path, seed: int) -> None:
    parsed = []
    for item in candidates:
        label, separator, source = item.partition("=")
        path = Path(source)
        if not separator or not label or not path.is_file():
            raise ValueError(f"expected LABEL=existing.wav, got: {item}")
        parsed.append((label, path))
    random.Random(seed).shuffle(parsed)
    output.mkdir(parents=True, exist_ok=True)
    mapping = {}
    sheet = []
    for index, (label, source) in enumerate(parsed):
        sample_id = chr(ord("A") + index)
        target = output / f"sample-{sample_id}{source.suffix.lower()}"
        shutil.copy2(source, target)
        mapping[sample_id] = {"label": label, "source": str(source.resolve())}
        sheet.append({
            "sample": sample_id,
            "electronic_timbre": None,
            "pronunciation": None,
            "pauses": None,
            "prosody": None,
            "usability": None,
            "decision": None,
            "notes": "",
        })
    (output / "scores.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "private-mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def summarize(directory: Path) -> None:
    scores = json.loads((directory / "scores.json").read_text(encoding="utf-8"))
    mapping = json.loads((directory / "private-mapping.json").read_text(encoding="utf-8"))
    dimensions = ["electronic_timbre", "pronunciation", "pauses", "prosody", "usability"]
    results = []
    for row in scores:
        missing = [key for key in dimensions + ["decision"] if row.get(key) in (None, "")]
        if missing:
            raise ValueError(f"sample {row.get('sample')} is missing: {', '.join(missing)}")
        values = [float(row[key]) for key in dimensions]
        results.append({
            "sample": row["sample"],
            "label": mapping[row["sample"]]["label"],
            "mean_score": round(sum(values) / len(values), 2),
            "decision": row["decision"],
            "scores": {key: row[key] for key in dimensions},
            "notes": row.get("notes", ""),
        })
    results.sort(key=lambda item: item["mean_score"], reverse=True)
    (directory / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("prepare")
    create.add_argument("--candidate", action="append", required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--seed", type=int, default=20260929)
    report = subparsers.add_parser("summarize")
    report.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.candidate, args.output, args.seed)
    else:
        summarize(args.directory)


if __name__ == "__main__":
    main()
