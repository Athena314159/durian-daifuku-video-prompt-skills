#!/usr/bin/env python3
"""Prepare a deterministic, zero-generation-cost scale guide for one daifuku shot."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


SKILL_DIR = Path(__file__).resolve().parent.parent
RELEASE_PATH = SKILL_DIR / "references" / "skill-release.json"
RATIO_RANGES = {
    "index_finger_mid": (3.5, 4.0),
    "palm": (0.75, 0.80),
    "mouth_width": (1.4, 1.6),
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def resolve(project_dir: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else project_dir / path


def relative_or_absolute(project_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project_dir))
    except ValueError:
        return str(path)


def find_shot(manifest: dict[str, Any], shot_id: str) -> dict[str, Any]:
    matches = [shot for shot in manifest.get("shots") or [] if isinstance(shot, dict) and shot.get("id") == shot_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one shot {shot_id!r}; found {len(matches)}")
    return matches[0]


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.expanduser().resolve()
    project = read_json(project_dir / "project.json")
    if project.get("product_profile") != "durian-daifuku-v2":
        raise ValueError("Pixel preflight only applies to durian-daifuku-v2")
    release = read_json(RELEASE_PATH)
    lock = project.get("skill_release_lock") if isinstance(project.get("skill_release_lock"), dict) else {}
    if lock.get("bundle_release_id") != release.get("bundle_release_id"):
        raise ValueError("LEGACY_PROJECT_GENERATION_BLOCKED: explicitly migrate the project before pixel preflight")
    product_path = project_dir / "library" / "product_bible.json"
    product = read_json(product_path)
    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = read_json(manifest_path)
    shot = find_shot(manifest, args.shot_id)
    source_value = (shot.get("asset_links") or {}).get("source_first_frame")
    source = resolve(project_dir, source_value)
    if not source.is_file():
        raise FileNotFoundError(f"Exact source first frame is unavailable: {source}")

    image = Image.open(source).convert("RGB")
    frame_width, frame_height = image.size
    anchor_type = args.anchor_type
    state = shot.get("product_state") if isinstance(shot.get("product_state"), dict) else {}
    scale_lock = state.get("scale_lock") if isinstance(state.get("scale_lock"), dict) else {}
    declared_anchor = scale_lock.get("anchor") if isinstance(scale_lock.get("anchor"), dict) else {}
    if declared_anchor.get("type") != anchor_type:
        raise ValueError("anchor-type must exactly match product_state.scale_lock.anchor.type")
    anchor_box = [int(value) for value in args.anchor_bbox]
    if len(anchor_box) != 4:
        raise ValueError("anchor-bbox must be x y width height")
    anchor_x, anchor_y, anchor_box_width, anchor_box_height = anchor_box
    if (
        anchor_x < 0
        or anchor_y < 0
        or anchor_box_width <= 0
        or anchor_box_height <= 0
        or anchor_x + anchor_box_width > frame_width
        or anchor_y + anchor_box_height > frame_height
    ):
        raise ValueError(f"anchor-bbox {anchor_box} must stay inside frame {[frame_width, frame_height]}")
    anchor_width = float(anchor_box_width)
    if anchor_width <= 0:
        raise ValueError("anchor-width-px must be positive")

    if anchor_type in RATIO_RANGES:
        ratio_min, ratio_max = RATIO_RANGES[anchor_type]
        selected_ratio = float(args.selected_ratio)
        if not ratio_min <= selected_ratio <= ratio_max:
            raise ValueError(f"selected-ratio for {anchor_type} must be within {ratio_min}–{ratio_max}")
        target_min = round(anchor_width * ratio_min)
        target_max = round(anchor_width * ratio_max)
        target_width = round(anchor_width * selected_ratio)
        ratio_basis = [ratio_min, ratio_max]
    elif anchor_type == "known_container_dimension":
        physical_cm = float(args.anchor_physical_cm or 0)
        if physical_cm <= 0:
            raise ValueError("known_container_dimension requires --anchor-physical-cm")
        target_width = round(anchor_width * 7.0 / physical_cm)
        target_min = round(anchor_width * 7.0 / physical_cm)
        target_max = round(anchor_width * 7.5 / physical_cm)
        selected_ratio = 7.0 / physical_cm
        ratio_basis = [7.0, 7.5]
    elif anchor_type == "approved_scene_scale_master":
        target_width = int(args.target_width_px or 0)
        if target_width <= 0:
            raise ValueError("approved_scene_scale_master requires --target-width-px")
        target_min = round(target_width * 0.95)
        target_max = round(target_width * 1.05)
        selected_ratio = target_width / anchor_width
        ratio_basis = [target_min / anchor_width, target_max / anchor_width]
    else:
        raise ValueError(f"Unsupported anchor type: {anchor_type}")

    target_height = round(target_width * float(args.height_ratio))
    center_x, center_y = args.target_center
    x = round(center_x - target_width / 2)
    y = round(center_y - target_height / 2)
    bbox = [x, y, target_width, target_height]
    if x < 0 or y < 0 or x + target_width > frame_width or y + target_height > frame_height:
        raise ValueError(f"Target bbox {bbox} exceeds frame {[frame_width, frame_height]}")

    guide_dir = project_dir / "review" / "scale-guides"
    guide_dir.mkdir(parents=True, exist_ok=True)
    guide_path = guide_dir / f"{args.shot_id}-daifuku-scale-guide.png"
    overlay = image.convert("RGBA")
    drawing = ImageDraw.Draw(overlay, "RGBA")
    drawing.rectangle(
        (anchor_x, anchor_y, anchor_x + anchor_box_width, anchor_y + anchor_box_height),
        outline=(255, 190, 0, 255),
        width=max(2, round(frame_width / 400)),
    )
    drawing.ellipse((x, y, x + target_width, y + target_height), fill=(0, 210, 255, 48), outline=(0, 225, 255, 255), width=max(3, round(frame_width / 300)))
    drawing.line((center_x - 12, center_y, center_x + 12, center_y), fill=(255, 80, 80, 255), width=3)
    drawing.line((center_x, center_y - 12, center_x, center_y + 12), fill=(255, 80, 80, 255), width=3)
    drawing.rectangle((x, max(0, y - 24), min(frame_width - 1, x + 210), y), fill=(0, 0, 0, 180))
    drawing.text((x + 4, max(0, y - 20)), f"TARGET {target_width}x{target_height}px", fill=(255, 255, 255, 255))
    overlay.convert("RGB").save(guide_path, quality=95)

    prepared_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    report_path = guide_dir / f"{args.shot_id}-daifuku-scale-guide.json"
    pixel_plan = {
        "status": "authorized",
        "prepared_at": prepared_at,
        "source_frame": relative_or_absolute(project_dir, source),
        "source_frame_sha256": sha256_file(source),
        "frame_size_px": [frame_width, frame_height],
        "anchor": {
            "type": anchor_type,
            "measured_width_px": anchor_width,
            "expected_ratio": declared_anchor.get("expected_ratio"),
            "selected_ratio": selected_ratio,
            "evidence": args.evidence,
            "measurement_method": "annotated_bbox",
            "measurement_bbox_xywh": anchor_box,
        },
        "target": {
            "width_px": target_width,
            "height_px": target_height,
            "width_tolerance_px": [target_min, target_max],
            "bbox_xywh": bbox,
            "center_xy": [center_x, center_y],
            "height_ratio": float(args.height_ratio),
            "dimension_basis": "reconstructed_whole" if state.get("state") in {"bitten", "hand_torn", "knife_cut", "two_halves_display"} else "visible_whole",
        },
        "contract_binding": {
            "bundle_release_id": release.get("bundle_release_id"),
            "product_profile": project.get("product_profile"),
            "product_version": product.get("version"),
            "product_bible_sha256": sha256_file(product_path),
            "state": state.get("state"),
            "anchor_type": declared_anchor.get("type"),
            "anchor_expected_ratio": declared_anchor.get("expected_ratio"),
        },
        "guide_path": relative_or_absolute(project_dir, guide_path),
        "guide_sha256": sha256_file(guide_path),
        "guide_role": "geometry_only_do_not_render_overlay",
        "manifest_path": relative_or_absolute(project_dir, report_path),
    }
    write_json(report_path, {"schema_version": "daifuku-pixel-preflight-v1.1", **pixel_plan})
    state = shot.setdefault("product_state", {})
    scale_lock = state.setdefault("scale_lock", {})
    scale_lock["pixel_plan"] = pixel_plan
    shot.setdefault("asset_links", {})["scale_guide"] = pixel_plan["guide_path"]
    write_json(manifest_path, manifest)
    return pixel_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the required pixel scale plan before daifuku image generation.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--shot-id", required=True)
    parser.add_argument("--anchor-type", required=True, choices=(*RATIO_RANGES.keys(), "known_container_dimension", "approved_scene_scale_master"))
    parser.add_argument("--anchor-bbox", required=True, type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--selected-ratio", type=float, default=3.75)
    parser.add_argument("--anchor-physical-cm", type=float)
    parser.add_argument("--target-width-px", type=int)
    parser.add_argument("--target-center", required=True, type=int, nargs=2, metavar=("X", "Y"))
    parser.add_argument("--height-ratio", type=float, default=0.9)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    result = prepare(args)
    print(json.dumps({"status": result["status"], "target": result["target"], "guide_path": result["guide_path"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
