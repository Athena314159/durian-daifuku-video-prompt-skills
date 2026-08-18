#!/usr/bin/env python3
"""Create timestamped contact sheets from extracted interval or scene frames."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError as exc:
    raise SystemExit(
        "Pillow is required for contact sheets. Run this script with the Codex bundled Python or install Pillow in the active environment."
    ) from exc


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def resolve(project_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_dir / path


def candidate_list(manifest: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    candidates = manifest.get(f"{source}_candidates") or []
    if candidates:
        return [item for item in candidates if isinstance(item, dict) and item.get("frame")]
    frames = manifest.get(f"{source}_frames") or []
    return [{"frame": frame, "time": None} for frame in frames]


def make_sheets(
    project_dir: Path,
    source: str,
    columns: int,
    rows: int,
    thumb_width: int,
) -> List[Path]:
    project_dir = project_dir.expanduser().resolve()
    manifest_path = project_dir / "source" / "source_manifest.json"
    manifest = load_json(manifest_path)
    candidates = candidate_list(manifest, source)
    if not candidates:
        raise ValueError(f"No {source} frames found. Run extract_video_assets.py first.")
    if columns <= 0 or rows <= 0 or thumb_width < 80:
        raise ValueError("columns/rows must be positive and thumb-width must be at least 80.")

    first_path = resolve(project_dir, candidates[0]["frame"])
    with Image.open(first_path) as first_image:
        ratio = first_image.height / first_image.width
    thumb_height = max(80, int(round(thumb_width * ratio)))
    label_height = 30
    gap = 8
    margin = 12
    cell_width = thumb_width + gap
    cell_height = thumb_height + label_height + gap
    page_width = margin * 2 + columns * cell_width - gap
    page_height = margin * 2 + rows * cell_height - gap
    per_page = columns * rows

    output_dir = project_dir / "source" / "contact_sheets"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    font = ImageFont.load_default()
    outputs: List[Path] = []

    for page_index in range(math.ceil(len(candidates) / per_page)):
        page = Image.new("RGB", (page_width, page_height), "#F4F1EB")
        draw = ImageDraw.Draw(page)
        page_items = candidates[page_index * per_page : (page_index + 1) * per_page]
        for item_index, item in enumerate(page_items):
            row, column = divmod(item_index, columns)
            x = margin + column * cell_width
            y = margin + row * cell_height
            image_path = resolve(project_dir, item["frame"])
            with Image.open(image_path) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                thumb = ImageOps.contain(image, (thumb_width, thumb_height), Image.Resampling.LANCZOS)
                tile = Image.new("RGB", (thumb_width, thumb_height), "black")
                tile.paste(thumb, ((thumb_width - thumb.width) // 2, (thumb_height - thumb.height) // 2))
                page.paste(tile, (x, y))
            global_index = page_index * per_page + item_index + 1
            time_value = item.get("time")
            time_label = f"{float(time_value):.2f}s" if isinstance(time_value, (int, float)) else "time unknown"
            draw.text((x + 4, y + thumb_height + 7), f"#{global_index:03d}  {time_label}", fill="#171717", font=font)

        output = output_dir / f"{source}_{run_id}_{page_index + 1:02d}.jpg"
        page.save(output, quality=92, optimize=True)
        outputs.append(output)

    manifest.setdefault("contact_sheets", []).extend(
        {
            "source": source,
            "path": str(path.relative_to(project_dir)),
            "created_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        }
        for path in outputs
    )
    write_json(manifest_path, manifest)
    return outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create compact timestamped frame-review sheets.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--source", choices=["interval", "scene"], default="interval")
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--rows", type=int, default=5)
    parser.add_argument("--thumb-width", type=int, default=220)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        outputs = make_sheets(args.project_dir, args.source, args.columns, args.rows, args.thumb_width)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps([str(path) for path in outputs], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
