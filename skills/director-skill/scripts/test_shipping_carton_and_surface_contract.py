#!/usr/bin/env python3
"""Regression tests for adaptive carton sizing and pixel-bound daifuku surface QA."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent


def run(*args: str) -> tuple[int, dict]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(result.stdout + result.stderr) from exc
    return result.returncode, payload


def carton_regressions(tmp: Path) -> None:
    planner = ROOT / "prepare_shipping_carton_capacity_plan.py"
    output = tmp / "carton-ok.json"
    code, payload = run(
        sys.executable, str(planner), "--retail-box-count", "12", "--source-width-cm", "47",
        "--source-depth-cm", "34", "--source-height-cm", "18", "--output", str(output),
    )
    assert code == 0 and payload["status"] == "authorized"
    assert payload["capacity_check"]["retail_box_dimensions_cm"] == [15.0, 15.0, 4.5]
    assert payload["capacity_check"]["capacity"] >= 12
    assert payload["inferred_outer_dimensions_cm"][0] >= 30

    blocked = tmp / "carton-blocked.json"
    code, payload = run(
        sys.executable, str(planner), "--retail-box-count", "12", "--max-outer-dimensions-cm", "20,20,10",
        "--output", str(blocked),
    )
    assert code == 2 and payload["status"] == "blocked"
    assert payload["error_code"] == "SOURCE_CONTAINER_CAPACITY_CONFLICT"


def surface_regressions(tmp: Path) -> None:
    auditor = ROOT / "audit_daifuku_surface.py"
    reference = tmp / "reference.png"
    good = tmp / "good.png"
    bad = tmp / "bad.png"
    Image.new("RGB", (256, 256), (218, 211, 195)).save(reference)
    Image.new("RGB", (256, 256), (216, 208, 192)).save(good)
    image = Image.new("RGB", (256, 256), (175, 160, 140))
    draw = ImageDraw.Draw(image)
    for y in range(0, 256, 4):
        for x in range(0, 256, 4):
            shade = 45 if (x // 4 + y // 4) % 2 else -35
            base = (175, 160, 140)
            draw.rectangle((x, y, x + 2, y + 2), fill=tuple(max(0, min(255, c + shade)) for c in base))
    image.save(bad)

    good_report = tmp / "good.json"
    code, payload = run(
        sys.executable, str(auditor), "--candidate", str(good), "--reference", str(reference),
        "--product-bbox", "0", "0", "256", "256", "--reference-bbox", "0", "0", "256", "256",
        "--output", str(good_report),
    )
    assert code == 0 and payload["pass"] is True

    bad_report = tmp / "bad.json"
    code, payload = run(
        sys.executable, str(auditor), "--candidate", str(bad), "--reference", str(reference),
        "--product-bbox", "0", "0", "256", "256", "--reference-bbox", "0", "0", "256", "256",
        "--output", str(bad_report),
    )
    assert code == 2 and payload["pass"] is False
    assert "DAIFUKU_SHELL_COLOR_CAST_INVALID" in payload["failed_checks"]
    assert "DAIFUKU_SURFACE_STONE_TEXTURE_INVALID" in payload["failed_checks"]


def main() -> int:
    with tempfile.TemporaryDirectory() as value:
        tmp = Path(value)
        carton_regressions(tmp)
        surface_regressions(tmp)
    print("shipping carton and surface regressions: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
