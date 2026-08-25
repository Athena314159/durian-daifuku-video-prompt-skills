#!/usr/bin/env python3
"""Regression tests for deterministic structured branch merge."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from test_validate_branch_handoff import build_contract, write


SCRIPT = Path(__file__).resolve().parent / "merge_branch_handoffs.py"


def run(image: Path, text: Path, locked: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--image-handoff", str(image), "--text-handoff", str(text),
         "--locked-shot-map", str(locked), "--out", str(out)],
        capture_output=True, text=True, check=False,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="handoff-merge-v2-") as temporary:
        root = Path(temporary)
        locked_path, text_path, image_path, _, _, image_handoff = build_contract(root)
        out1, out2 = root / "merged-1.json", root / "merged-2.json"
        first = run(image_path, text_path, locked_path, out1)
        assert first.returncode == 0, first.stdout + first.stderr
        second = run(image_path, text_path, locked_path, out2)
        assert second.returncode == 0, second.stdout + second.stderr
        assert out1.read_bytes() == out2.read_bytes(), "merge output is not deterministic"
        package = json.loads(out1.read_text(encoding="utf-8"))
        assert package["schema_version"] == "full-delivery-merged-v2.0"
        assert [item["unit_id"] for item in package["cards"]] == package["collections"]["unit_ids"]
        assert len(package["controller_gallery"]["entries"]) > len(package["collections"]["unit_ids"])
        assert len(package["cards"][0]["approved_assets"]) == 2
        assert package["controller_gallery"]["must_inline_images"] is True
        assert package["controller_gallery"]["may_only_report_path"] is False
        assert package["controller_gallery"]["deliver_when_ready"] is True
        assert "alignment_table" not in json.dumps(package, ensure_ascii=False)

        bad = copy.deepcopy(image_handoff)
        bad["collections"]["unit_ids"][0:2] = reversed(bad["collections"]["unit_ids"][0:2])
        write(image_path, bad)
        blocked_out = root / "must-not-exist.json"
        blocked = run(image_path, text_path, locked_path, blocked_out)
        assert blocked.returncode == 2
        assert not blocked_out.exists()
        assert "canonical order" in blocked.stdout or "branches differ" in blocked.stdout

    print("DETERMINISTIC HANDOFF MERGE TESTS PASSED (2 cases)")


if __name__ == "__main__":
    main()
