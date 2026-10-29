"""Verify that the working package matches the tested old-wording runtime."""

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    """Save source fingerprints and fail if the adopted runtime has drifted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    experiment = root / "log/prompt-holdout-2026-10-28"
    manifest_path = experiment / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reference = experiment / "runtime/old"
    rows = {}
    for name in sorted(manifest["runtime"]):
        selected = root / name
        rows[name] = {
            "expected_sha256": hashlib.sha256((reference / name).read_bytes()).hexdigest(),
            "actual_sha256": hashlib.sha256(selected.read_bytes()).hexdigest() if selected.is_file() else None,
        }
    mismatches = [name for name, row in rows.items() if row["expected_sha256"] != row["actual_sha256"]]
    report = {
        "selection": "corrected_assembly_with_old_wording",
        "reference": reference.relative_to(root).as_posix(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "files_checked": len(rows),
        "matches_tested_runtime": not mismatches,
        "mismatches": mismatches,
        "files": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, ensure_ascii=False))
    raise SystemExit(bool(mismatches))


if __name__ == "__main__":
    main()
