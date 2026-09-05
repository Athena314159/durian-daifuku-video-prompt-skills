#!/usr/bin/env python3
"""Regression tests for hand-relative seven-centimetre scale QA."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from PIL import Image

from audit_daifuku_scale_relationship import audit


def namespace(candidate: Path, output: Path, *, product_width: int, finger_widths: list[int], palm_widths: list[int] | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        candidate=candidate,
        product_bbox=[100, 100, product_width, 300],
        finger_bbox=[[20, 500 + index * 50, value, 40] for index, value in enumerate(finger_widths)],
        palm_bbox=[[20, 700 + index * 50, value, 40] for index, value in enumerate(palm_widths or [])],
        output=output,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        image = root / "candidate.png"
        Image.new("RGB", (1000, 1200), (220, 210, 200)).save(image)

        too_small = audit(namespace(image, root / "too-small.json", product_width=490, finger_widths=[155, 150]))
        assert too_small["pass"] is False
        assert too_small["error_code"] == "DAIFUKU_SCALE_ANCHOR_CONFLICT"
        assert all(record["product_to_anchor_width_ratio"] < 4.0 for record in too_small["anchors"])

        valid = audit(namespace(image, root / "valid.json", product_width=420, finger_widths=[100, 98]))
        assert valid["pass"] is True
        assert valid["error_code"] is None
        assert valid["anchor_count"] == 2

        missing = audit(namespace(image, root / "missing.json", product_width=420, finger_widths=[100]))
        assert missing["pass"] is False
        assert missing["error_code"] == "DAIFUKU_SCALE_RELATIONSHIP_EVIDENCE_MISSING"

    print(json.dumps({"status": "ok", "contract": "daifuku-scale-relationship"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
