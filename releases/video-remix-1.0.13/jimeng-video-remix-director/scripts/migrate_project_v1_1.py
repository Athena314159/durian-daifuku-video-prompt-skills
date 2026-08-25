#!/usr/bin/env python3
"""Non-destructively migrate a v1.0 project to the transcript-first v1.1 schema."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


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


def deep_merge_defaults(defaults: Any, current: Any) -> Any:
    """Fill missing schema fields while preserving every existing/custom project value."""
    if isinstance(defaults, dict):
        current_dict = current if isinstance(current, dict) else {}
        return {
            key: deep_merge_defaults(default_value, current_dict[key]) if key in current_dict else copy.deepcopy(default_value)
            for key, default_value in defaults.items()
        } | {key: copy.deepcopy(value) for key, value in current_dict.items() if key not in defaults}
    return copy.deepcopy(current if current is not None else defaults)


def normalized_prompt_length_contract_for_storage(raw: Any) -> Dict[str, Any]:
    """Migrate the project-owned double gate without leaving a half-enabled state."""
    value = raw if isinstance(raw, dict) else {}
    if value.get("enabled") is not True:
        return {
            "enabled": False,
            "minimum_non_whitespace_characters": 0,
            "maximum_non_whitespace_characters": 0,
        }
    minimum = value.get("minimum_non_whitespace_characters")
    maximum = value.get("maximum_non_whitespace_characters")
    if minimum in (None, 0) and maximum in (None, 0):
        minimum, maximum = 3000, 4000
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or minimum < 1
        or maximum < minimum
    ):
        raise ValueError("Cannot migrate a half-enabled Prompt length contract; provide both positive bounds or disable it")
    return {
        "enabled": True,
        "minimum_non_whitespace_characters": minimum,
        "maximum_non_whitespace_characters": maximum,
    }


def atomic_map_recoverability(source: Dict[str, Any], manifest: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Return whether a legacy project already proves canonical SRC/ADD coverage.

    Migration never invents atomic source cuts.  If exact one-time coverage
    cannot be proven from persisted data, the migrated project is explicitly
    stopped at rebuild instead of silently treating legacy S clips as SRC rows.
    """
    source_shots = source.get("source_shots")
    if not isinstance(source_shots, list) or not source_shots:
        return False, "Cannot recover canonical SRC/ADD: source_manifest has no authoritative atomic source_shots inventory."
    source_ids: list[str] = []
    for index, source_shot in enumerate(source_shots):
        source_id = str((source_shot or {}).get("id") or "") if isinstance(source_shot, dict) else ""
        if not re.fullmatch(r"SRC\d+", source_id) or source_id in source_ids:
            return False, f"Cannot recover canonical SRC/ADD: source_shots[{index}] has a missing, duplicate or non-canonical SRC id."
        timecode = source_shot.get("timecode") or {}
        if not all(isinstance(timecode.get(key), (int, float)) for key in ("start", "end", "duration")):
            return False, f"Cannot recover canonical SRC/ADD: {source_id} has no exact persisted timecode."
        if not isinstance(source_shot.get("start_frame"), int) or not isinstance(source_shot.get("end_frame"), int):
            return False, f"Cannot recover canonical SRC/ADD: {source_id} has no persisted start_frame/end_frame."
        source_ids.append(source_id)

    flattened_source_ids: list[str] = []
    inserted_ids: list[str] = []
    shots = manifest.get("shots")
    if not isinstance(shots, list) or not shots:
        return False, "Cannot recover canonical SRC/ADD: shot_manifest has no generation clips."
    for shot_index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            return False, f"Cannot recover canonical SRC/ADD: shots[{shot_index}] is not an object."
        source_units = shot.get("source_units")
        inserted_units = shot.get("inserted_units")
        if not isinstance(source_units, list) or not isinstance(inserted_units, list) or not (source_units or inserted_units):
            return False, f"Cannot recover canonical SRC/ADD: shots[{shot_index}] lacks explicit source_units/inserted_units."
        for unit in source_units:
            source_id = str((unit or {}).get("source_shot_id") or "") if isinstance(unit, dict) else ""
            if source_id not in source_ids:
                return False, f"Cannot recover canonical SRC/ADD: shots[{shot_index}] refers to unknown SRC {source_id or '<missing>'}."
            flattened_source_ids.append(source_id)
        for unit in inserted_units:
            inserted_id = str((unit or {}).get("inserted_shot_id") or "") if isinstance(unit, dict) else ""
            if not re.fullmatch(r"ADD\d+", inserted_id) or inserted_id in inserted_ids:
                return False, f"Cannot recover canonical SRC/ADD: shots[{shot_index}] has a missing, duplicate or non-canonical ADD id."
            inserted_ids.append(inserted_id)
    if flattened_source_ids != source_ids:
        return False, "Cannot recover canonical SRC/ADD: source_units do not preserve every SRC exactly once and in source order."
    return True, None


def migrate(project_dir: Path) -> Dict[str, Any]:
    project_dir = project_dir.expanduser().resolve()
    required = [project_dir / "project.json", project_dir / "shots" / "shot_manifest.json"]
    if not all(path.is_file() for path in required):
        raise FileNotFoundError(f"Not a Jimeng project: {project_dir}")

    original_project = load_json(project_dir / "project.json")
    project_defaults = load_json(TEMPLATE_DIR / "project.json")
    normalized_migrated_prompt_contract = normalized_prompt_length_contract_for_storage(
        deep_merge_defaults(
            project_defaults.get("prompt_length_contract") or {},
            original_project.get("prompt_length_contract") or {},
        )
    )
    migration_id = datetime.now().strftime("v1.1-%Y%m%dT%H%M%S-%f")
    backup_dir = project_dir / "migration" / migration_id
    backup_dir.mkdir(parents=True, exist_ok=False)

    template_json_paths = sorted(TEMPLATE_DIR.rglob("*.json"))
    relative_template_paths = [path.relative_to(TEMPLATE_DIR) for path in template_json_paths]
    for relative in relative_template_paths:
        source_path = project_dir / relative
        if source_path.is_file():
            target = backup_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)

    # Apply every current template default recursively.  Existing values and
    # custom keys win; newly introduced nested rules are never dropped.
    for template_path, relative in zip(template_json_paths, relative_template_paths):
        target = project_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            write_json(target, deep_merge_defaults(load_json(template_path), load_json(target)))
        else:
            shutil.copy2(template_path, target)

    product_library_path = project_dir / "library" / "product_library.json"
    product_bible_path = project_dir / "library" / "product_bible.json"
    product_rule_overrides: Dict[str, Any] = {}
    if product_library_path.is_file() and product_bible_path.is_file():
        product_library = load_json(product_library_path)
        product_bible = load_json(product_bible_path)
        if isinstance(product_bible.get("project_rule_overrides"), dict):
            product_rule_overrides = product_bible["project_rule_overrides"]
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
                    "states": sorted(((product_bible.get("state_profiles") or product_bible.get("states") or {})).keys()),
                    "reference_assets": [],
                    "approved_result_assets": [],
                }
            ]
            write_json(product_library_path, product_library)

    project_path = project_dir / "project.json"
    project = load_json(project_path)
    project["schema_version"] = "1.1"
    project["project_rules"] = deep_merge_defaults(project_defaults.get("project_rules") or {}, project.get("project_rules") or {})
    for key, value in product_rule_overrides.items():
        project["project_rules"][key] = copy.deepcopy(value)
    if "product_mode" not in original_project:
        project["product_mode"] = "replace_product" if project.get("product_profile") else "preserve_source_product"
    project["prompt_length_contract"] = normalized_migrated_prompt_contract
    write_json(project_path, project)

    source_path = project_dir / "source" / "source_manifest.json"
    source = deep_merge_defaults(load_json(TEMPLATE_DIR / "source" / "source_manifest.json"), load_json(source_path))
    source["schema_version"] = "1.1"
    if "first_frame" in source:
        if not source.get("video_first_frame"):
            source["video_first_frame"] = source.get("first_frame")
        source.pop("first_frame", None)
    for run in source.get("analysis_runs") or []:
        if isinstance(run, dict) and "first_frame" in run:
            if not run.get("video_first_frame"):
                run["video_first_frame"] = run.get("first_frame")
            run.pop("first_frame", None)
    write_json(source_path, source)

    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = deep_merge_defaults(load_json(TEMPLATE_DIR / "shots" / "shot_manifest.json"), load_json(manifest_path))
    manifest["schema_version"] = "1.1"
    story_path = project_dir / "planning" / "story_plan.json"
    story = deep_merge_defaults(load_json(TEMPLATE_DIR / "planning" / "story_plan.json"), load_json(story_path))
    existing_segments = [item for item in (story.get("segments") or []) if isinstance(item, dict)]
    existing_segment_ids = {str(item.get("id")) for item in existing_segments if item.get("id")}
    migrated_segments = list(existing_segments)
    for index, shot in enumerate(manifest.get("shots") or [], start=1):
        if not isinstance(shot, dict):
            continue
        state = (shot.get("product_state") or {}).get("state")
        character = shot.setdefault("character", {})
        has_character = any(character.get(key) for key in ("identity", "position", "gaze"))
        visual_type = "person_eating" if state == "bitten" else ("person_product_showcase" if has_character else "product_showcase")
        shot["visual_type"] = shot.get("visual_type") or visual_type
        shot["narrative_role"] = shot.get("narrative_role") or ("hook" if index == 1 else ("eating_experience" if visual_type == "person_eating" else "visual_proof"))
        shot["scene_rationale"] = shot.get("scene_rationale") or "沿用原片场景以保持构图和生活化视觉连续性。"
        character.setdefault("present", visual_type != "product_showcase")

        audio = shot.setdefault("audio", {})
        if audio.get("delivery_mode") in {"voiceover", "on_screen_speech", "silent"}:
            delivery_mode = audio["delivery_mode"]
        elif audio.get("on_screen_speech") is True:
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

        segment_ids = [str(value) for value in (shot.get("script_segment_ids") or []) if str(value).strip()]
        if not segment_ids:
            segment_ids = [f"T{index:03d}"]
            shot["script_segment_ids"] = segment_ids
        for segment_id in segment_ids:
            if segment_id not in existing_segment_ids:
                migrated_segments.append(
                    {
                        "id": segment_id,
                        "text": text or "待用户提供字幕",
                        "delivery_mode": delivery_mode,
                        "delivery_rationale": "待结合用户字幕稿和原片风格确认。",
                        "assigned_shots": [shot.get("id")],
                    }
                )
                existing_segment_ids.add(segment_id)

        assets = shot.setdefault("asset_links", {})
        if not assets.get("source_first_frame") and assets.get("source_frame"):
            assets["source_first_frame"] = assets.get("source_frame")
        assets.pop("source_frame", None)
        assets["beauty_keyframe_candidates"] = assets.get("beauty_keyframe_candidates") or []
        assets["selected_beauty_keyframe"] = assets.get("selected_beauty_keyframe")
        if not assets.get("approved_generation_first_frame") and assets.get("approved_first_frame"):
            assets["approved_generation_first_frame"] = assets.get("approved_first_frame")
        assets.pop("approved_first_frame", None)
        assets.pop("generated_video", None)
        assets.setdefault("avatar_reference", None)

    recoverable, rebuild_reason = atomic_map_recoverability(source, manifest)
    manifest["migration_status"] = {
        "requires_manual_shot_map_rebuild": not recoverable,
        "reason": rebuild_reason,
        "required_action": None if recoverable else "Rebuild the atomic source timeline from frame 0 to the authoritative video end, then map every SRC exactly once and every real insertion to a unique ADD.",
    }
    project["migration_requirements"] = manifest["migration_status"]
    write_json(project_path, project)
    write_json(manifest_path, manifest)

    story["segments"] = migrated_segments
    subtitle_script = story.setdefault("subtitle_script", {})
    if not subtitle_script.get("text"):
        recovered_text = "\n".join(
            str(item.get("text"))
            for item in migrated_segments
            if item.get("text") not in (None, "", "待用户提供字幕")
        ) or None
        subtitle_script["text"] = recovered_text
        subtitle_script["provided_by_user"] = False
    write_json(story_path, story)
    return {
        "project_dir": str(project_dir),
        "backup_dir": str(backup_dir),
        "shot_count": len(manifest.get("shots") or []),
        "template_json_files_merged": len(template_json_paths),
        "requires_manual_shot_map_rebuild": not recoverable,
        "rebuild_reason": rebuild_reason,
    }


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
