#!/usr/bin/env python3
"""Remove black canvas and blurred padding from a source-video frame.

The common input is a portrait frame that contains a sharp 9:16 picture inside a
larger black canvas, sometimes with blurred copies above or below the real
picture.  The script first finds the non-black rectangle, then selects the
sharpest 9:16 vertical window inside it.  It can also use an audited manual crop.

This is deterministic preprocessing, not generative editing.  It writes a JSON
report so the operator can verify the crop before using the result as an image
edit target.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--crop", help="Audited x,y,width,height override")
    parser.add_argument("--target-width", type=int, default=1080)
    parser.add_argument("--target-height", type=int, default=1920)
    parser.add_argument("--dark-threshold", type=int, default=24)
    parser.add_argument("--edge-active-fraction", type=float, default=0.12)
    return parser.parse_args()


def black_bounds(image: Image.Image, threshold: int, active_fraction: float) -> tuple[int, int, int, int]:
    gray = image.convert("L")
    width, height = gray.size
    px = gray.load()
    active_rows = []
    for y in range(height):
        active = sum(px[x, y] > threshold for x in range(width)) / width
        if active >= active_fraction:
            active_rows.append(y)
    active_cols = []
    for x in range(width):
        active = sum(px[x, y] > threshold for y in range(height)) / height
        if active >= active_fraction:
            active_cols.append(x)
    if not active_rows or not active_cols:
        raise ValueError("No non-black picture region detected")
    return min(active_cols), min(active_rows), max(active_cols) + 1, max(active_rows) + 1


def sharpness(image: Image.Image) -> float:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(edges).mean[0])


def choose_nine_sixteen(image: Image.Image, bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top
    target_height = round(width * 16 / 9)
    if target_height <= height:
        span = height - target_height
        # Sample every two pixels plus the final legal origin.  A 64 px inset
        # suppresses the hard transition line between the true picture and a
        # blurred filler band.
        origins = list(range(top, bottom - target_height + 1, 2))
        if origins[-1] != bottom - target_height:
            origins.append(bottom - target_height)
        best = None
        for y in origins:
            tile = image.crop((left, y, right, y + target_height))
            inset = min(64, max(0, target_height // 12))
            core = tile.crop((0, inset, width, target_height - inset))
            score = sharpness(core)
            # Prefer a stable crop near the non-black region centre when two
            # positions have nearly identical detail.
            centre_penalty = abs((y - top) - span / 2) / max(1, height) * 0.15
            score -= centre_penalty
            if best is None or score > best[0]:
                best = (score, y)
        assert best is not None
        return left, best[1], width, target_height

    target_width = round(height * 9 / 16)
    if target_width > width:
        raise ValueError("Detected picture region cannot contain a 9:16 crop")
    x = left + (width - target_width) // 2
    return x, top, target_width, height


def parse_crop(value: str) -> tuple[int, int, int, int]:
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise argparse.ArgumentTypeError("--crop must be x,y,width,height")
    return tuple(parts)  # type: ignore[return-value]


def perimeter_dark_fraction(image: Image.Image, threshold: int, band: int = 4) -> float:
    gray = image.convert("L")
    width, height = gray.size
    px = gray.load()
    samples = []
    for y in range(min(band, height)):
        samples.extend(px[x, y] for x in range(width))
        samples.extend(px[x, height - 1 - y] for x in range(width))
    for x in range(min(band, width)):
        samples.extend(px[x, y] for y in range(height))
        samples.extend(px[width - 1 - x, y] for y in range(height))
    return sum(value <= threshold for value in samples) / max(1, len(samples))


def main() -> None:
    args = parse_args()
    image = Image.open(args.input).convert("RGB")
    detected = black_bounds(image, args.dark_threshold, args.edge_active_fraction)
    if args.crop:
        x, y, width, height = parse_crop(args.crop)
        method = "manual_audited_crop"
    else:
        x, y, width, height = choose_nine_sixteen(image, detected)
        method = "auto_sharpest_9x16_inside_nonblack"
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        raise ValueError("Crop exceeds input bounds")
    ratio_error = abs(width / height - 9 / 16)
    if ratio_error > 0.002:
        raise ValueError(f"Crop is not 9:16 enough: {width}x{height}")

    cropped = image.crop((x, y, x + width, y + height))
    resized = cropped.resize((args.target_width, args.target_height), Image.Resampling.LANCZOS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    resized.save(args.output, quality=95)

    report = {
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "input_size": [image.width, image.height],
        "detected_nonblack_bounds_xyxy": list(detected),
        "crop_xywh": [x, y, width, height],
        "method": method,
        "output_size": [args.target_width, args.target_height],
        "output_aspect": round(args.target_width / args.target_height, 6),
        "target_aspect": round(9 / 16, 6),
        "perimeter_dark_fraction_before": round(perimeter_dark_fraction(image, args.dark_threshold), 6),
        "perimeter_dark_fraction_after": round(perimeter_dark_fraction(resized, args.dark_threshold), 6),
        "status": "candidate_requires_visual_review",
        "review_required": [
            "no black border on any edge",
            "no blurred duplicate filler inside the retained picture",
            "no subject, hand, product, plate, package, or subtitle cut by crop",
            "straight scene lines remain straight",
        ],
    }
    report_path = args.report or args.output.with_suffix(".crop.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
