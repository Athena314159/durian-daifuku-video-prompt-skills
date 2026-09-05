#!/usr/bin/env python3
"""Infer a plausible shipping-carton size without shrinking fixed retail boxes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


BOX = (15.0, 15.0, 4.5)
CLEARANCE = 0.6
BOARD_ALLOWANCE = 0.8


def triple(value: str) -> tuple[float, float, float]:
    parts = tuple(float(item) for item in value.split(","))
    if len(parts) != 3 or any(item <= 0 for item in parts):
        raise argparse.ArgumentTypeError("expected three positive comma-separated dimensions")
    return parts


def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retail-box-count", type=int, required=True)
    parser.add_argument("--source-width-cm", type=float)
    parser.add_argument("--source-depth-cm", type=float)
    parser.add_argument("--source-height-cm", type=float)
    parser.add_argument("--source-frame")
    parser.add_argument("--source-frame-sha256")
    parser.add_argument("--source-carton-bbox", type=int, nargs=4)
    parser.add_argument("--scene-anchor")
    parser.add_argument("--max-outer-dimensions-cm", type=triple)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.retail_box_count <= 0:
        parser.error("retail-box-count must be positive")

    source_dims = (args.source_width_cm, args.source_depth_cm, args.source_height_cm)
    has_source_dims = all(isinstance(value, (int, float)) and value > 0 for value in source_dims)
    candidates = []
    limit = args.retail_box_count
    for nx in range(1, limit + 1):
        for ny in range(1, math.ceil(limit / nx) + 1):
            nz = math.ceil(limit / (nx * ny))
            capacity = nx * ny * nz
            inner = (nx * BOX[0] + (nx + 1) * CLEARANCE, ny * BOX[1] + (ny + 1) * CLEARANCE, nz * BOX[2] + (nz + 1) * CLEARANCE)
            outer = tuple(round(value + 2 * BOARD_ALLOWANCE, 1) for value in inner)
            if args.max_outer_dimensions_cm and any(value > ceiling for value, ceiling in zip(outer, args.max_outer_dimensions_cm)):
                continue
            compactness = outer[0] * outer[1] * outer[2]
            source_penalty = 0.0
            if has_source_dims:
                source_penalty = sum(abs(math.log(value / target)) for value, target in zip(outer, source_dims)) * 10000
            candidates.append((source_penalty + compactness, capacity, (nx, ny, nz), inner, outer))

    base = {
        "schema_version": "shipping-carton-capacity-plan-v1.0",
        "retail_box_dimensions_cm": list(BOX),
        "retail_box_dimensions_fixed": True,
        "product_scale_may_shrink_to_fit": False,
        "retail_box_count": args.retail_box_count,
        "source_evidence": {
            "source_frame": args.source_frame,
            "source_frame_sha256": args.source_frame_sha256,
            "source_carton_bbox_xywh": args.source_carton_bbox,
            "same_depth_scene_anchor": args.scene_anchor,
            "estimated_source_outer_dimensions_cm": list(source_dims) if has_source_dims else None,
        },
        "hard_scene_max_outer_dimensions_cm": list(args.max_outer_dimensions_cm) if args.max_outer_dimensions_cm else None,
        "decision_rule": "infer_or_resize_carton_first; block_only_when_no_layout_fits_an_explicit_scene_max",
    }
    if not candidates:
        payload = {
            **base,
            "status": "blocked",
            "generation_authorized": False,
            "error_code": "SOURCE_CONTAINER_CAPACITY_CONFLICT",
            "detail": "固定15×15×4.5厘米零售盒在已声明的场景硬尺寸上限内没有可行装箱布局；禁止缩小产品或零售盒，停止生成。",
        }
        exit_code = 2
    else:
        _, capacity, grid, inner, outer = min(candidates, key=lambda item: item[0])
        payload = {
            **base,
            "status": "authorized",
            "generation_authorized": True,
            "inferred_outer_dimensions_cm": list(outer),
            "inferred_inner_dimensions_cm": [round(value, 1) for value in inner],
            "layout": {"columns": grid[0], "rows": grid[1], "layers": grid[2], "orientation": "retail_boxes_flat_with_physical_clearance"},
            "capacity_check": {"retail_box_dimensions_cm": list(BOX), "required_count": args.retail_box_count, "capacity": capacity, "pass": True},
            "inference_rationale": "纸箱尺寸由原片同景深容器/手/桌面锚点、固定零售盒尺寸、数量与紧凑装箱共同推断；允许纸箱随场景放大或缩小，禁止反向缩小零售盒或大福。",
        }
        exit_code = 0
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    payload["plan_sha256"] = canonical_hash(payload)
    args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.expanduser().resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
