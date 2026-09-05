#!/usr/bin/env python3
"""Audit a generated daifuku against two same-depth hand anchors.

The deterministic pixel preflight controls the requested product box. This
second audit prevents a generator from making the hands much larger and thus
turning a nominal seven-centimetre product into a visually three-centimetre
one. A hand-interaction result needs two independent physical relationships.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


FINGER_RATIO = (4.0, 4.4)
PALM_RATIO = (0.88, 0.96)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_bbox(box: list[int], width: int, height: int) -> bool:
    if len(box) != 4:
        return False
    x, y, box_width, box_height = box
    return x >= 0 and y >= 0 and box_width > 0 and box_height > 0 and x + box_width <= width and y + box_height <= height


def bbox_record(kind: str, box: list[int], product_width: int, expected: tuple[float, float]) -> dict[str, Any]:
    anchor_width = int(box[2])
    ratio = product_width / anchor_width if anchor_width > 0 else 0.0
    return {
        "type": kind,
        "bbox_xywh": box,
        "measured_width_px": anchor_width,
        "product_to_anchor_width_ratio": round(ratio, 4),
        "expected_ratio": list(expected),
        "pass": expected[0] <= ratio <= expected[1],
    }


def audit(args: argparse.Namespace) -> dict[str, Any]:
    candidate = args.candidate.expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    with Image.open(candidate) as image:
        width, height = image.size
    product = [int(value) for value in args.product_bbox]
    if not valid_bbox(product, width, height):
        raise ValueError("product-bbox must stay inside the generated frame")

    anchors: list[dict[str, Any]] = []
    for raw in args.finger_bbox or []:
        box = [int(value) for value in raw]
        if not valid_bbox(box, width, height):
            raise ValueError(f"finger-bbox must stay inside the generated frame: {box}")
        anchors.append(bbox_record("index_finger_mid", box, product[2], FINGER_RATIO))
    for raw in args.palm_bbox or []:
        box = [int(value) for value in raw]
        if not valid_bbox(box, width, height):
            raise ValueError(f"palm-bbox must stay inside the generated frame: {box}")
        anchors.append(bbox_record("palm", box, product[2], PALM_RATIO))

    independent_types = {record["type"] for record in anchors}
    enough_anchors = len(anchors) >= 2
    relationship_pass = enough_anchors and all(record["pass"] for record in anchors)
    failed_code = None
    if not enough_anchors:
        failed_code = "DAIFUKU_SCALE_RELATIONSHIP_EVIDENCE_MISSING"
    elif not relationship_pass:
        failed_code = "DAIFUKU_SCALE_ANCHOR_CONFLICT"

    result = {
        "schema_version": "durian-daifuku-scale-relationship-qa-v1.0",
        "candidate": {
            "path": str(candidate),
            "sha256": sha256_file(candidate),
            "size_px": [width, height],
        },
        "product_bbox_xywh": product,
        "minimum_anchor_count": 2,
        "anchor_count": len(anchors),
        "independent_anchor_types": sorted(independent_types),
        "anchors": anchors,
        "pass": relationship_pass,
        "error_code": failed_code,
        "rule": "手部互动结果中，完整或可重建大福宽度须为每个同景深食指中段宽度的4.0–4.4倍；若使用掌宽，须为掌宽的0.88–0.96；至少两个物理锚点同时通过。",
        "audited_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
    }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit generated daifuku size against two same-depth hand anchors.")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--product-bbox", required=True, type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--finger-bbox", action="append", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--palm-bbox", action="append", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = audit(args)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
