#!/usr/bin/env python3
"""Non-destructively migrate a v1.0 project to the transcript-first v1.1 schema."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "project-template"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def migrate(project_dir: Path) -> Dict[str, Any]:
    project_dir = project_dir.expanduser().resolve()
    required = [project_dir / "project.json", project_dir / "shots" / "shot_manifest.json"]
    if not all(path.is_file() for path in required):
        raise FileNotFoundError(f"Not a Jimeng project: {project_dir}")

    migration_id = datetime.now().strftime("v1.1-%Y%m%dT%H%M%S")
    backup_dir = project_dir / "migration" / migration_id
    backup_dir.mkdir(parents=True, exist_ok=False)
    for relative in ("project.json", "source/source_manifest.json", "shots/shot_manifest.json"):
        source = project_dir / relative
        if source.is_file():
            target = backup_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    for relative in (
        "planning/story_plan.json",
        "planning/asset_reuse_plan.json",
        "library/avatar_library.json",
        "library/product_library.json",
        "library/knowledge_index.json",
    ):
        target = project_dir / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(TEMPLATE_DIR / relative, target)

    product_library_path = project_dir / "library" / "product_library.json"
    product_bible_path = project_dir / "library" / "product_bible.json"
    if product_library_path.is_file() and product_bible_path.is_file():
        product_library = load_json(product_library_path)
        product_bible = load_json(product_bible_path)
        profile_id = product_bible.get("profile_id") or "unknown-product"
        if not product_library.get("products"):
            product_library["products"] = [
                {
                    "id": profile_id,
                    "name": product_bible.get("name", profile_id),
                    "active": True,
                    "rights_cleared": False,
                    "usage_scope": "internal_test",
                    "profile_path": "library/product_bible.json",
                    "version": product_bible.get("version", 1),
                    "states": sorted((product_bible.get("states") or {}).keys()),
                    "reference_assets": [],
                    "approved_result_assets": [],
                }
            ]
            write_json(product_library_path, product_library)

    project_path = project_dir / "project.json"
    project = load_json(project_path)
    old_rules = project.get("project_rules") or {}
    project["schema_version"] = "1.1"
    project["project_rules"] = {
        "preserve_source_composition": old_rules.get("preserve_source_composition", True),
        "speech_strategy": "adaptive_from_script_and_source",
        "allow_on_screen_speech": True,
        "allow_voiceover": True,
        "lip_sync_only_when_speaking": True,
        "subtitles_generated_by_model": old_rules.get("subtitles_generated_by_model", False),
        "watermark_allowed": old_rules.get("watermark_allowed", False),
        "packaging_visible": old_rules.get("packaging_visible", False),
    }
    write_json(project_path, project)

    source_path = project_dir / "source" / "source_manifest.json"
    if source_path.is_file():
        source = load_json(source_path)
        source["schema_version"] = "1.1"
        if "first_frame" in source and "video_first_frame" not in source:
            source["video_first_frame"] = source.pop("first_frame")
        for run in source.get("analysis_runs") or []:
            if isinstance(run, dict) and "first_frame" in run and "video_first_frame" not in run:
                run["video_first_frame"] = run.pop("first_frame")
        write_json(source_path, source)

    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = load_json(manifest_path)
    manifest["schema_version"] = "1.1"
    segments = []
    for index, shot in enumerate(manifest.get("shots") or [], start=1):
        state = (shot.get("product_state") or {}).get("state")
        character = shot.setdefault("character", {})
        has_character = any(character.get(key) for key in ("identity", "position", "gaze"))
        visual_type = "person_eating" if state == "bitten" else ("person_product_showcase" if has_character else "product_showcase")
        shot["visual_type"] = shot.get("visual_type") or visual_type
        shot["narrative_role"] = shot.get("narrative_role") or ("hook" if index == 1 else ("eating_experience" if visual_type == "person_eating" else "visual_proof"))
        shot["scene_rationale"] = shot.get("scene_rationale") or "沿用原片场景以保持构图和生活化视觉连续性。"
        character["present"] = visual_type != "product_showcase"

        audio = shot.setdefault("audio", {})
        if audio.get("on_screen_speech") is True:
            delivery_mode = "on_screen_speech"
        elif audio.get("voiceover") is True:
            delivery_mode = "voiceover"
        else:
            delivery_mode = "silent"
        text = audio.get("voiceover_text") or audio.get("script_text") or ""
        audio.update(
            {
                "delivery_mode": delivery_mode,
                "delivery_rationale": audio.get("delivery_rationale") or "待结合用户字幕稿和原片风格确认。",
                "script_text": text,
                "speech_timing": audio.get("speech_timing"),
            }
        )
        for key in ("on_screen_speech", "lip_sync", "voiceover", "voiceover_text"):
            audio.pop(key, None)

        segment_id = f"T{index:03d}"
        shot["script_segment_ids"] = shot.get("script_segment_ids") or [segment_id]
        segments.append(
            {
                "id": segment_id,
                "text": text or "待用户提供字幕",
                "delivery_mode": delivery_mode,
                "delivery_rationale": "待结合用户字幕稿和原片风格确认。",
                "assigned_shots": [shot.get("id")],
            }
        )

        assets = shot.setdefault("asset_links", {})
        assets["source_first_frame"] = assets.pop("source_frame", None)
        assets["beauty_keyframe_candidates"] = assets.get("beauty_keyframe_candidates") or []
        assets["selected_beauty_keyframe"] = assets.get("selected_beauty_keyframe")
        assets["approved_generation_first_frame"] = assets.pop("approved_first_frame", None)
        assets.pop("generated_video", None)
        assets.setdefault("avatar_reference", None)
    write_json(manifest_path, manifest)

    story_path = project_dir / "planning" / "story_plan.json"
    story = load_json(story_path)
    story["segments"] = segments
    story["subtitle_script"]["text"] = "\n".join(item["text"] for item in segments if item["text"] != "待用户提供字幕") or None
    story["subtitle_script"]["provided_by_user"] = False
    write_json(story_path, story)
    return {"project_dir": str(project_dir), "backup_dir": str(backup_dir), "shot_count": len(manifest.get("shots") or [])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate a Jimeng project to schema v1.1 without deleting old files.")
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.project_dir), ensure_ascii=False, indent=2))
    except (FileNotFoundError, FileExistsError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
