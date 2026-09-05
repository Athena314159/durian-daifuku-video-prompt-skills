#!/usr/bin/env python3
"""Build a labeled contact sheet from a directory of already prepared frames."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--thumb-width", type=int, default=216)
    parser.add_argument("--label-map", type=Path, help="Optional UTF-8 lines: filename<TAB>label")
    args = parser.parse_args()

    files = sorted(
        path for path in args.input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not files:
        raise SystemExit("No images found")
    labels = {}
    if args.label_map:
        for line in args.label_map.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            name, label = line.split("\t", 1)
            labels[name] = label

    with Image.open(files[0]) as first:
        ratio = first.height / first.width
    thumb_height = round(args.thumb_width * ratio)
    label_height, gap, margin = 34, 10, 16
    rows = math.ceil(len(files) / args.columns)
    width = margin * 2 + args.columns * args.thumb_width + (args.columns - 1) * gap
    height = margin * 2 + rows * (thumb_height + label_height) + (rows - 1) * gap
    sheet = Image.new("RGB", (width, height), "#f5f3ee")
    draw = ImageDraw.Draw(sheet)
    chinese_font = Path("/System/Library/Fonts/STHeiti Light.ttc")
    font = ImageFont.truetype(str(chinese_font), 18) if chinese_font.exists() else ImageFont.load_default(size=18)

    for index, path in enumerate(files):
        row, col = divmod(index, args.columns)
        x = margin + col * (args.thumb_width + gap)
        y = margin + row * (thumb_height + label_height + gap)
        with Image.open(path) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            tile = ImageOps.fit(source, (args.thumb_width, thumb_height), Image.Resampling.LANCZOS)
        sheet.paste(tile, (x, y))
        label = labels.get(path.name, path.stem)
        draw.text((x + 3, y + thumb_height + 6), label, fill="#111111", font=font)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, quality=94, optimize=True)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
