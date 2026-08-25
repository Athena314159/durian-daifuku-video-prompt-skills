#!/usr/bin/env python3
"""Deterministically project an approved package-face master onto box geometry.

The image model is allowed to create the box geometry, folds, hands, lighting
and shadows.  Readable print is supplied by this tool from an approved flat
master so that text and artwork are not redrawn, mirrored or silently omitted.

The target quad order is top-left, top-right, bottom-right, bottom-left.  An
optional output-sized grayscale visible mask may preserve hands, folds or
natural off-frame regions: white means the projected face remains visible and
black means keep the candidate image unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_quad(value: str) -> list[tuple[float, float]]:
    try:
        numbers = [float(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("quad must contain eight comma-separated numbers") from exc
    if len(numbers) != 8:
        raise argparse.ArgumentTypeError("quad must contain x1,y1,x2,y2,x3,y3,x4,y4")
    points = [(numbers[index], numbers[index + 1]) for index in range(0, 8, 2)]
    area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % 4]
        area += x1 * y2 - x2 * y1
    if abs(area) < 1.0:
        raise argparse.ArgumentTypeError("quad is degenerate or too small")
    return points


def homography(source: list[tuple[float, float]], target: list[tuple[float, float]]) -> np.ndarray:
    """Return H such that target ~= H * source in homogeneous coordinates."""
    rows: list[list[float]] = []
    values: list[float] = []
    for (x, y), (u, v) in zip(source, target, strict=True):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        values.append(u)
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        values.append(v)
    coefficients = np.linalg.solve(np.asarray(rows, dtype=np.float64), np.asarray(values, dtype=np.float64))
    return np.asarray(
        [
            [coefficients[0], coefficients[1], coefficients[2]],
            [coefficients[3], coefficients[4], coefficients[5]],
            [coefficients[6], coefficients[7], 1.0],
        ],
        dtype=np.float64,
    )


def pil_inverse_coefficients(forward: np.ndarray) -> tuple[float, ...]:
    inverse = np.linalg.inv(forward)
    inverse /= inverse[2, 2]
    return (
        float(inverse[0, 0]),
        float(inverse[0, 1]),
        float(inverse[0, 2]),
        float(inverse[1, 0]),
        float(inverse[1, 1]),
        float(inverse[1, 2]),
        float(inverse[2, 0]),
        float(inverse[2, 1]),
    )


def preserve_candidate_lighting(projected: Image.Image, candidate: Image.Image, strength: float) -> Image.Image:
    if strength <= 0:
        return projected
    projected_array = np.asarray(projected, dtype=np.float32).copy()
    candidate_gray = np.asarray(candidate.convert("L"), dtype=np.float32)
    factor = 1.0 + strength * ((candidate_gray - 128.0) / 128.0)
    projected_array[..., :3] = np.clip(projected_array[..., :3] * factor[..., None], 0, 255)
    return Image.fromarray(projected_array.astype(np.uint8), "RGBA")


def main() -> int:
    parser = argparse.ArgumentParser(description="Project approved package artwork onto one visible box face.")
    parser.add_argument("--candidate", required=True, type=Path, help="Candidate image containing approved box geometry.")
    parser.add_argument("--master", required=True, type=Path, help="Approved flat front/side/top face master.")
    parser.add_argument("--quad", required=True, type=parse_quad, help="TL,TR,BR,BL target corners as eight comma-separated values.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--face", required=True, choices=("front", "side", "top"))
    parser.add_argument("--visible-mask", type=Path, help="Optional output-sized grayscale mask; white=project, black=preserve candidate.")
    parser.add_argument("--opacity", type=float, default=1.0)
    parser.add_argument("--edge-feather", type=float, default=0.6)
    parser.add_argument("--lighting-strength", type=float, default=0.22)
    parser.add_argument("--manifest", type=Path, help="Internal audit JSON; defaults beside output.")
    args = parser.parse_args()

    candidate_path = args.candidate.expanduser().resolve()
    master_path = args.master.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    if not candidate_path.is_file():
        parser.error(f"candidate not found: {candidate_path}")
    if not master_path.is_file():
        parser.error(f"master not found: {master_path}")
    if not 0.0 < args.opacity <= 1.0:
        parser.error("opacity must be in (0, 1]")
    if not 0.0 <= args.lighting_strength <= 1.0:
        parser.error("lighting-strength must be in [0, 1]")
    if args.edge_feather < 0:
        parser.error("edge-feather must be non-negative")

    candidate = Image.open(candidate_path).convert("RGBA")
    master = Image.open(master_path).convert("RGBA")
    source_corners = [
        (0.0, 0.0),
        (float(master.width - 1), 0.0),
        (float(master.width - 1), float(master.height - 1)),
        (0.0, float(master.height - 1)),
    ]
    forward = homography(source_corners, args.quad)
    coefficients = pil_inverse_coefficients(forward)
    projected = master.transform(
        candidate.size,
        Image.Transform.PERSPECTIVE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    projected = preserve_candidate_lighting(projected, candidate, args.lighting_strength)

    alpha = projected.getchannel("A")
    if args.edge_feather:
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=args.edge_feather))
    if args.opacity < 1.0:
        alpha = alpha.point(lambda value: round(value * args.opacity))

    mask_path: Path | None = None
    if args.visible_mask:
        mask_path = args.visible_mask.expanduser().resolve()
        if not mask_path.is_file():
            parser.error(f"visible mask not found: {mask_path}")
        visible = Image.open(mask_path).convert("L")
        if visible.size != candidate.size:
            parser.error("visible mask dimensions must equal candidate dimensions")
        alpha = Image.fromarray(
            ((np.asarray(alpha, dtype=np.uint16) * np.asarray(visible, dtype=np.uint16)) // 255).astype(np.uint8),
            "L",
        )
    projected.putalpha(alpha)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    composite = Image.alpha_composite(candidate, projected)
    if output_path.suffix.lower() in {".jpg", ".jpeg"}:
        composite.convert("RGB").save(output_path, quality=95, subsampling=0)
    else:
        composite.save(output_path)

    manifest_path = args.manifest.expanduser().resolve() if args.manifest else output_path.with_suffix(output_path.suffix + ".projection.json")
    manifest = {
        "schema_version": "package-master-projection-v1.0",
        "created_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "face": args.face,
        "projection_method": "homography",
        "candidate": {"path": str(candidate_path), "sha256": sha256(candidate_path), "size": list(candidate.size)},
        "master": {"path": str(master_path), "sha256": sha256(master_path), "size": list(master.size)},
        "visible_mask": None if mask_path is None else {"path": str(mask_path), "sha256": sha256(mask_path)},
        "target_quad_tl_tr_br_bl": [[round(x, 4), round(y, 4)] for x, y in args.quad],
        "opacity": args.opacity,
        "edge_feather": args.edge_feather,
        "lighting_strength": args.lighting_strength,
        "output": {"path": str(output_path), "sha256": sha256(output_path), "size": list(composite.size)},
        "model_redraw_used": False,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
