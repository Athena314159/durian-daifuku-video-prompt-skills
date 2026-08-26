#!/usr/bin/env python3
"""Hash-bound pixel audit for warm milky shell color and non-stone surface texture."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    value = rgb.astype(np.float64) / 255.0
    value = np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)
    xyz = value @ np.array([[0.4124564, 0.2126729, 0.0193339], [0.3575761, 0.7151522, 0.1191920], [0.1804375, 0.0721750, 0.9503041]])
    xyz /= np.array([0.95047, 1.0, 1.08883])
    delta = 6 / 29
    f = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29)
    return np.stack((116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])), axis=1)


def crop_pixels(path: Path, box: list[int]) -> tuple[np.ndarray, np.ndarray]:
    image = np.asarray(Image.open(path).convert("RGB"))
    x, y, width, height = box
    if x < 0 or y < 0 or width <= 0 or height <= 0 or y + height > image.shape[0] or x + width > image.shape[1]:
        raise ValueError("bbox outside image")
    crop = image[y:y + height, x:x + width]
    yy, xx = np.ogrid[:height, :width]
    mask = ((xx - width / 2) / (width * 0.39)) ** 2 + ((yy - height / 2) / (height * 0.39)) ** 2 <= 1
    pixels = crop[mask]
    gray = crop.astype(np.float64).mean(axis=2)
    residual = np.abs(gray[1:, 1:] - (gray[:-1, 1:] + gray[1:, :-1] + gray[:-1, :-1]) / 3)
    texture = residual[mask[1:, 1:]]
    return pixels, texture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--product-bbox", type=int, nargs=4, required=True)
    parser.add_argument("--reference-bbox", type=int, nargs=4, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = args.candidate.expanduser().resolve()
    reference = args.reference.expanduser().resolve()
    candidate_pixels, candidate_texture = crop_pixels(candidate, args.product_bbox)
    reference_pixels, reference_texture = crop_pixels(reference, args.reference_bbox)
    candidate_lab = np.median(srgb_to_lab(candidate_pixels), axis=0)
    reference_lab = np.median(srgb_to_lab(reference_pixels), axis=0)
    delta_e = float(np.linalg.norm(candidate_lab - reference_lab))
    texture_p90 = float(np.percentile(candidate_texture, 90))
    reference_texture_p90 = float(np.percentile(reference_texture, 90))
    color_pass = bool(delta_e <= 6.0 and candidate_lab[0] >= 76.0 and abs(candidate_lab[1] - reference_lab[1]) <= 5.0 and abs(candidate_lab[2] - reference_lab[2]) <= 6.0)
    texture_limit = max(4.0, reference_texture_p90 * 2.5 + 2.0)
    texture_pass = bool(texture_p90 <= texture_limit)
    failed = []
    if not color_pass:
        failed.append("DAIFUKU_SHELL_COLOR_CAST_INVALID")
    if not texture_pass:
        failed.append("DAIFUKU_SURFACE_STONE_TEXTURE_INVALID")
    payload = {
        "schema_version": "durian-daifuku-surface-qa-v1.0",
        "candidate": {"path": str(candidate), "sha256": sha256(candidate), "bbox_xywh": args.product_bbox},
        "approved_reference": {"path": str(reference), "sha256": sha256(reference), "bbox_xywh": args.reference_bbox},
        "color_check": {"candidate_median_lab": [round(v, 3) for v in candidate_lab], "reference_median_lab": [round(v, 3) for v in reference_lab], "delta_e76": round(delta_e, 3), "maximum_delta_e76": 6.0, "pass": color_pass},
        "texture_check": {"candidate_high_frequency_p90": round(texture_p90, 3), "reference_high_frequency_p90": round(reference_texture_p90, 3), "maximum_allowed": round(texture_limit, 3), "pass": texture_pass},
        "pass": color_pass and texture_pass,
        "failed_checks": failed,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.expanduser().resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
