#!/usr/bin/env python3
"""Lint and compile a structured Jimeng video-remix project."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


REQUIRED_FILES = {
    "project": Path("project.json"),
    "product": Path("library/product_bible.json"),
    "product_library": Path("library/product_library.json"),
    "style": Path("library/style_bible.json"),
    "corrections": Path("library/correction_memory.json"),
    "knowledge": Path("library/knowledge_index.json"),
    "avatars": Path("library/avatar_library.json"),
    "story": Path("planning/story_plan.json"),
    "asset_reuse": Path("planning/asset_reuse_plan.json"),
    "source": Path("source/source_manifest.json"),
    "shots": Path("shots/shot_manifest.json"),
}

VALID_RISKS = {"low", "medium", "high"}
VALID_SCOPES = {"shot", "project", "product", "style"}
VALID_VISUAL_TYPES = {"product_showcase", "person_product_showcase", "person_eating"}
VALID_DELIVERY_MODES = {"voiceover", "on_screen_speech", "silent"}
COMMERCIAL_CLEARANCE_FIELDS = (
    "source_rights_cleared",
    "portrait_rights_cleared",
    "music_rights_cleared",
    "claims_approved",
)


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(value.rstrip() + "\n")
    temp_path.replace(path)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def spoken_char_count(value: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", value))


def flatten_text(values: Any) -> List[str]:
    output: List[str] = []
    for value in as_list(values):
        if isinstance(value, str) and value.strip():
            output.append(value.strip())
    return output


def contains_positive_without_negative(text: str, positive_terms: Sequence[str], negative_terms: Sequence[str]) -> bool:
    normalized = text.lower()
    if not any(term.lower() in normalized for term in positive_terms):
        return False
    return not any(term.lower() in normalized for term in negative_terms)


def join_cn(values: Any, fallback: str = "未指定") -> str:
    items = flatten_text(values)
    return "；".join(items) if items else fallback


def table_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def add_issue(issues: List[Dict[str, Any]], level: str, code: str, path: str, message: str) -> None:
    issues.append({"level": level, "code": code, "path": path, "message": message})


def read_bundle(project_dir: Path) -> Dict[str, Dict[str, Any]]:
    bundle: Dict[str, Dict[str, Any]] = {}
    for key, relative_path in REQUIRED_FILES.items():
        bundle[key] = load_json(project_dir / relative_path)
    return bundle


def resolve_path(project_dir: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_dir / path


def validate_required_files(project_dir: Path, issues: List[Dict[str, Any]]) -> bool:
    valid = True
    for key, relative_path in REQUIRED_FILES.items():
        full_path = project_dir / relative_path
        if not full_path.is_file():
            add_issue(issues, "ERROR", "missing_file", str(relative_path), f"Missing required {key} file.")
            valid = False
            continue
        try:
            load_json(full_path)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            add_issue(issues, "ERROR", "invalid_json", str(relative_path), str(exc))
            valid = False
    return valid


def validate_story_plan(story: Dict[str, Any], issues: List[Dict[str, Any]]) -> None:
    subtitle = story.get("subtitle_script") or {}
    if subtitle.get("provided_by_user") is not True:
        add_issue(issues, "ERROR", "subtitle_script_required", "planning/story_plan.json.subtitle_script", "A user-provided subtitle script is required before delivery-mode planning.")
    if not has_text(subtitle.get("text")) and not has_text(subtitle.get("path")):
        add_issue(issues, "ERROR", "subtitle_script_empty", "planning/story_plan.json.subtitle_script", "Store the subtitle text or its project-relative path.")

    assessment = story.get("source_style_assessment") or {}
    if assessment.get("delivery_style") not in {"voiceover_dominant", "on_screen_speech_dominant", "mixed", "silent", "unknown"}:
        add_issue(issues, "ERROR", "invalid_source_delivery_style", "planning/story_plan.json.source_style_assessment.delivery_style", "Use a supported source delivery style.")
    if assessment.get("delivery_style") == "unknown":
        add_issue(issues, "ERROR", "source_style_not_assessed", "planning/story_plan.json.source_style_assessment", "Assess the original video's delivery style before compiling prompts.")

    logic = story.get("narrative_logic") or {}
    for field in ("hook", "product_promise", "visual_proof", "eating_experience", "closing_payoff"):
        if not has_text(logic.get(field)):
            add_issue(issues, "ERROR", "missing_narrative_logic", f"planning/story_plan.json.narrative_logic.{field}", "Define the video's story function before shot prompting.")

    strategy = story.get("delivery_strategy") or {}
    if strategy.get("mode") not in {"voiceover_dominant", "on_screen_speech_dominant", "mixed", "silent"}:
        add_issue(issues, "ERROR", "delivery_strategy_undecided", "planning/story_plan.json.delivery_strategy.mode", "Choose the delivery strategy from the subtitle script and original-video style.")
    if not has_text(strategy.get("rationale")):
        add_issue(issues, "ERROR", "missing_delivery_rationale", "planning/story_plan.json.delivery_strategy.rationale", "Explain why this speech/voice-over split fits the script and source style.")

    ratio_fields = ("voiceover_target_ratio", "on_screen_speech_target_ratio", "silent_target_ratio")
    ratios = [strategy.get(field) for field in ratio_fields]
    for field, value in zip(ratio_fields, ratios):
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            add_issue(issues, "ERROR", "invalid_delivery_ratio", f"planning/story_plan.json.delivery_strategy.{field}", "Ratio must be a number from 0 to 1.")
    if all(isinstance(value, (int, float)) for value in ratios) and abs(sum(ratios) - 1) > 0.03:
        add_issue(issues, "ERROR", "delivery_ratio_sum", "planning/story_plan.json.delivery_strategy", "Voice-over, on-screen speech and silent ratios must sum to 1.")

    targets = story.get("visual_mix_targets") or {}
    for visual_type in sorted(VALID_VISUAL_TYPES):
        target = targets.get(visual_type) or {}
        minimum, maximum = target.get("min_ratio"), target.get("max_ratio")
        if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)) or not 0 <= minimum <= maximum <= 1:
            add_issue(issues, "ERROR", "invalid_visual_mix_target", f"planning/story_plan.json.visual_mix_targets.{visual_type}", "min_ratio and max_ratio must define a valid 0-1 range.")

    pacing = story.get("pacing") or {}
    for field in (
        "opening_hook_seconds",
        "target_average_shot_seconds",
        "maximum_single_shot_seconds",
        "maximum_on_screen_chars_per_second",
        "maximum_voiceover_chars_per_second",
    ):
        value = pacing.get(field)
        if not isinstance(value, (int, float)) or value <= 0:
            add_issue(issues, "ERROR", "invalid_pacing_value", f"planning/story_plan.json.pacing.{field}", "Pacing values must be positive numbers.")

    segments = story.get("segments")
    if not isinstance(segments, list) or not segments:
        add_issue(issues, "ERROR", "missing_script_segments", "planning/story_plan.json.segments", "Split the user subtitle script into timed narrative segments.")
        return
    seen_ids = set()
    for index, segment in enumerate(segments):
        path = f"planning/story_plan.json.segments[{index}]"
        if not isinstance(segment, dict):
            add_issue(issues, "ERROR", "invalid_script_segment", path, "Segment must be an object.")
            continue
        segment_id = segment.get("id")
        if not has_text(segment_id) or segment_id in seen_ids:
            add_issue(issues, "ERROR", "invalid_script_segment_id", f"{path}.id", "Segment id is required and must be unique.")
        else:
            seen_ids.add(segment_id)
        if not has_text(segment.get("text")):
            add_issue(issues, "ERROR", "missing_script_segment_text", f"{path}.text", "Segment text is required.")
        if segment.get("delivery_mode") not in VALID_DELIVERY_MODES:
            add_issue(issues, "ERROR", "invalid_segment_delivery", f"{path}.delivery_mode", f"Use one of {sorted(VALID_DELIVERY_MODES)}.")
        if not has_text(segment.get("delivery_rationale")):
            add_issue(issues, "ERROR", "missing_segment_delivery_rationale", f"{path}.delivery_rationale", "Explain why this line is voice-over, on-screen speech or silent.")
        if not as_list(segment.get("assigned_shots")):
            add_issue(issues, "ERROR", "unassigned_script_segment", f"{path}.assigned_shots", "Assign every script segment to at least one shot.")


def validate_mix_and_pacing(story: Dict[str, Any], shots: List[Dict[str, Any]], issues: List[Dict[str, Any]]) -> None:
    durations = []
    visual_seconds = {key: 0.0 for key in VALID_VISUAL_TYPES}
    delivery_seconds = {key: 0.0 for key in VALID_DELIVERY_MODES}
    for shot in shots:
        duration = (shot.get("timecode") or {}).get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            continue
        duration_value = float(duration)
        durations.append(duration_value)
        visual_type = shot.get("visual_type")
        delivery_mode = (shot.get("audio") or {}).get("delivery_mode")
        if visual_type in visual_seconds:
            visual_seconds[visual_type] += duration_value
        if delivery_mode in delivery_seconds:
            delivery_seconds[delivery_mode] += duration_value

    total = sum(durations)
    if total <= 0:
        return
    targets = story.get("visual_mix_targets") or {}
    for visual_type, seconds in visual_seconds.items():
        ratio = seconds / total
        target = targets.get(visual_type) or {}
        minimum, maximum = target.get("min_ratio"), target.get("max_ratio")
        if isinstance(minimum, (int, float)) and ratio < minimum - 0.01:
            add_issue(issues, "WARN", "visual_mix_too_low", f"shots/shot_manifest.json.{visual_type}", f"{visual_type} occupies {ratio:.0%}, below the planned minimum {minimum:.0%}.")
        if isinstance(maximum, (int, float)) and ratio > maximum + 0.01:
            add_issue(issues, "WARN", "visual_mix_too_high", f"shots/shot_manifest.json.{visual_type}", f"{visual_type} occupies {ratio:.0%}, above the planned maximum {maximum:.0%}.")

    strategy = story.get("delivery_strategy") or {}
    target_map = {
        "voiceover": strategy.get("voiceover_target_ratio"),
        "on_screen_speech": strategy.get("on_screen_speech_target_ratio"),
        "silent": strategy.get("silent_target_ratio"),
    }
    for delivery_mode, seconds in delivery_seconds.items():
        actual = seconds / total
        target = target_map.get(delivery_mode)
        if isinstance(target, (int, float)) and abs(actual - target) > 0.15:
            add_issue(issues, "WARN", "delivery_ratio_drift", f"shots/shot_manifest.json.{delivery_mode}", f"Actual {delivery_mode} ratio is {actual:.0%}, materially different from the planned {target:.0%}.")

    pacing = story.get("pacing") or {}
    maximum = pacing.get("maximum_single_shot_seconds")
    if isinstance(maximum, (int, float)):
        for shot in shots:
            duration = (shot.get("timecode") or {}).get("duration")
            if isinstance(duration, (int, float)) and duration > maximum:
                add_issue(issues, "ERROR", "shot_duration_exceeded", f"shots/shot_manifest.json.{shot.get('id')}", f"Shot duration {duration}s exceeds the planned maximum {maximum}s; split the semantic/action loop or record an explicit user override before compiling.")
    first = min(shots, key=lambda item: (item.get("timecode") or {}).get("start", float("inf")), default=None)
    if first and first.get("narrative_role") != "hook":
        add_issue(issues, "WARN", "opening_without_hook", f"shots/shot_manifest.json.{first.get('id')}.narrative_role", "The opening shot should normally perform the hook role.")


def validate_project(project_dir: Path, bundle: Dict[str, Dict[str, Any]], issues: List[Dict[str, Any]]) -> None:
    project = bundle["project"]
    product = bundle["product"]
    style = bundle["style"]
    corrections = bundle["corrections"]
    shots = bundle["shots"]
    story = bundle["story"]
    knowledge = bundle["knowledge"]
    avatars = bundle["avatars"]
    product_library = bundle["product_library"]

    for field in ("project_id", "project_name", "platform", "aspect_ratio", "generation_mode", "product_profile", "style_profile"):
        if not has_text(project.get(field)):
            add_issue(issues, "ERROR", "missing_project_field", f"project.json.{field}", "Required project field is empty.")

    if not project.get("source_video"):
        add_issue(issues, "ERROR", "missing_source_video", "project.json.source_video", "Set the source video path after import.")
    else:
        source_path = resolve_path(project_dir, project.get("source_video"))
        if source_path is not None and not source_path.exists():
            add_issue(issues, "WARN", "source_video_unavailable", "project.json.source_video", f"Source path is not currently accessible: {source_path}")

    if project.get("product_profile") != product.get("profile_id"):
        add_issue(
            issues,
            "ERROR",
            "product_profile_mismatch",
            "library/product_bible.json.profile_id",
            "Product profile does not match project.json.",
        )
    if project.get("style_profile") != style.get("profile_id"):
        add_issue(
            issues,
            "ERROR",
            "style_profile_mismatch",
            "library/style_bible.json.profile_id",
            "Style profile does not match project.json.",
        )

    project_rules = project.get("project_rules") or {}
    if project_rules.get("speech_strategy") not in {"adaptive_from_script_and_source", "manual"}:
        add_issue(issues, "ERROR", "invalid_speech_strategy", "project.json.project_rules.speech_strategy", "Use adaptive_from_script_and_source or manual.")
    if project_rules.get("allow_voiceover") is False and project_rules.get("allow_on_screen_speech") is False:
        add_issue(issues, "WARN", "no_narration_mode", "project.json.project_rules", "Both voice-over and on-screen speech are disabled.")

    rules = corrections.get("rules")
    if not isinstance(rules, list):
        add_issue(issues, "ERROR", "invalid_correction_rules", "library/correction_memory.json.rules", "rules must be a list.")
    else:
        seen_ids = set()
        for index, rule in enumerate(rules):
            path = f"library/correction_memory.json.rules[{index}]"
            if not isinstance(rule, dict):
                add_issue(issues, "ERROR", "invalid_correction_rule", path, "Rule must be an object.")
                continue
            rule_id = rule.get("id")
            if not has_text(rule_id):
                add_issue(issues, "ERROR", "missing_rule_id", path, "Rule id is required.")
            elif rule_id in seen_ids:
                add_issue(issues, "ERROR", "duplicate_rule_id", path, f"Duplicate rule id: {rule_id}")
            else:
                seen_ids.add(rule_id)
            if rule.get("scope") not in VALID_SCOPES:
                add_issue(issues, "ERROR", "invalid_rule_scope", path, f"scope must be one of {sorted(VALID_SCOPES)}.")
            priority = rule.get("priority")
            if not isinstance(priority, int) or not 1 <= priority <= 100:
                add_issue(issues, "ERROR", "invalid_rule_priority", path, "priority must be an integer from 1 to 100.")
            if not has_text(rule.get("instruction")):
                add_issue(issues, "ERROR", "missing_rule_instruction", path, "instruction is required.")

    validate_story_plan(story, issues)

    if not isinstance(knowledge.get("entries"), list):
        add_issue(issues, "ERROR", "invalid_knowledge_entries", "library/knowledge_index.json.entries", "entries must be a list.")
    if not isinstance(avatars.get("avatars"), list):
        add_issue(issues, "ERROR", "invalid_avatar_entries", "library/avatar_library.json.avatars", "avatars must be a list.")
    product_entries = product_library.get("products")
    if not isinstance(product_entries, list) or not product_entries:
        add_issue(issues, "ERROR", "invalid_product_library", "library/product_library.json.products", "Products must contain the selected project product.")
    elif not any(item.get("id") == project.get("product_profile") for item in product_entries if isinstance(item, dict)):
        add_issue(issues, "ERROR", "selected_product_missing_from_library", "library/product_library.json.products", "The project product_profile must exist in product_library.json.")

    shot_items = shots.get("shots")
    if not isinstance(shot_items, list):
        add_issue(issues, "ERROR", "invalid_shot_list", "shots/shot_manifest.json.shots", "shots must be a list.")
        return
    if not shot_items:
        add_issue(issues, "ERROR", "no_shots", "shots/shot_manifest.json.shots", "Add at least one analyzed shot.")
        return

    state_profiles = product.get("state_profiles") or {}
    seen_shot_ids = set()
    for index, shot in enumerate(shot_items):
        validate_shot(project_dir, project, style, state_profiles, story, shot, index, seen_shot_ids, issues)

    story_segments = story.get("segments") or []
    segment_ids = {segment.get("id") for segment in story_segments if isinstance(segment, dict) and has_text(segment.get("id"))}
    shot_ids = {shot.get("id") for shot in shot_items if isinstance(shot, dict) and has_text(shot.get("id"))}
    for shot in shot_items:
        if not isinstance(shot, dict):
            continue
        for segment_id in as_list(shot.get("script_segment_ids")):
            if segment_id not in segment_ids:
                add_issue(issues, "ERROR", "unknown_script_segment", f"shots/shot_manifest.json.{shot.get('id')}.script_segment_ids", f"Unknown story segment: {segment_id}")
    for segment in story_segments:
        if not isinstance(segment, dict):
            continue
        for shot_id in as_list(segment.get("assigned_shots")):
            if shot_id not in shot_ids:
                add_issue(issues, "ERROR", "unknown_assigned_shot", f"planning/story_plan.json.segments.{segment.get('id')}.assigned_shots", f"Unknown shot: {shot_id}")

    validate_mix_and_pacing(story, shot_items, issues)

    commercial = project.get("commercial") or {}
    intended_use = commercial.get("intended_use", "internal_test")
    if intended_use == "commercial_release":
        for field in COMMERCIAL_CLEARANCE_FIELDS:
            if commercial.get(field) is not True:
                add_issue(issues, "BLOCK", "commercial_clearance_missing", f"project.json.commercial.{field}", "Commercial release is blocked until this field is true.")
        if not has_text(commercial.get("reviewer")):
            add_issue(issues, "BLOCK", "commercial_reviewer_missing", "project.json.commercial.reviewer", "Name the release reviewer.")
    elif intended_use not in {"internal_test", "client_review"}:
        add_issue(issues, "ERROR", "invalid_intended_use", "project.json.commercial.intended_use", "Use internal_test, client_review, or commercial_release.")

    if project.get("status") == "approved" and any(issue["level"] in {"ERROR", "BLOCK"} for issue in issues):
        add_issue(issues, "ERROR", "invalid_approved_status", "project.json.status", "Project cannot remain approved while errors or commercial blocks exist.")


def validate_shot(
    project_dir: Path,
    project: Dict[str, Any],
    style: Dict[str, Any],
    state_profiles: Dict[str, Any],
    story: Dict[str, Any],
    shot: Any,
    index: int,
    seen_ids: set,
    issues: List[Dict[str, Any]],
) -> None:
    base = f"shots/shot_manifest.json.shots[{index}]"
    if not isinstance(shot, dict):
        add_issue(issues, "ERROR", "invalid_shot", base, "Shot must be an object.")
        return

    shot_id = shot.get("id")
    if not has_text(shot_id):
        add_issue(issues, "ERROR", "missing_shot_id", f"{base}.id", "Shot id is required.")
    elif shot_id in seen_ids:
        add_issue(issues, "ERROR", "duplicate_shot_id", f"{base}.id", f"Duplicate shot id: {shot_id}")
    else:
        seen_ids.add(shot_id)

    for field in ("title", "purpose", "narrative_role"):
        if not has_text(shot.get(field)):
            add_issue(issues, "ERROR", "missing_shot_field", f"{base}.{field}", "Required shot field is empty.")

    visual_type = shot.get("visual_type")
    if visual_type not in VALID_VISUAL_TYPES:
        add_issue(issues, "ERROR", "invalid_visual_type", f"{base}.visual_type", f"Use only {sorted(VALID_VISUAL_TYPES)}.")
    if not as_list(shot.get("script_segment_ids")):
        add_issue(issues, "ERROR", "missing_script_segment_links", f"{base}.script_segment_ids", "Bind the shot to one or more subtitle-script segments.")
    if not has_text(shot.get("scene_rationale")):
        add_issue(issues, "ERROR", "missing_scene_rationale", f"{base}.scene_rationale", "Explain why this scene supports the product, person or eating beat.")

    timecode = shot.get("timecode") or {}
    start, end, duration = timecode.get("start"), timecode.get("end"), timecode.get("duration")
    if not all(isinstance(value, (int, float)) for value in (start, end, duration)):
        add_issue(issues, "ERROR", "invalid_timecode", f"{base}.timecode", "start, end and duration must be numeric.")
    else:
        if end <= start or duration <= 0:
            add_issue(issues, "ERROR", "invalid_timecode_order", f"{base}.timecode", "end must be greater than start and duration must be positive.")
        if abs((end - start) - duration) > 0.08:
            add_issue(issues, "ERROR", "duration_mismatch", f"{base}.timecode", "duration must equal end - start within 0.08 seconds.")

    if not flatten_text(shot.get("source_facts")):
        add_issue(issues, "WARN", "missing_source_facts", f"{base}.source_facts", "Record what is directly observable in the source.")
    project_rules = project.get("project_rules") or {}
    if project_rules.get("preserve_source_composition") is True and not flatten_text(shot.get("source_locks")):
        add_issue(issues, "ERROR", "missing_source_locks", f"{base}.source_locks", "Source locks are required when preserving composition.")
    if not flatten_text(shot.get("allowed_changes")):
        add_issue(issues, "WARN", "missing_allowed_changes", f"{base}.allowed_changes", "Explicitly define what may change.")

    scene = shot.get("scene") or {}
    if not has_text(scene.get("location")):
        add_issue(issues, "ERROR", "missing_scene", f"{base}.scene.location", "Scene location is required.")
    if not flatten_text(scene.get("background")):
        add_issue(issues, "ERROR", "missing_background", f"{base}.scene.background", "Describe visible background elements.")

    character = shot.get("character") or {}
    emotion = shot.get("emotion") or {}
    if visual_type == "product_showcase":
        if character.get("present") is not False:
            add_issue(issues, "ERROR", "product_shot_has_character", f"{base}.character.present", "Product showcase shots must explicitly set character.present=false.")
    else:
        if character.get("present") is not True:
            add_issue(issues, "ERROR", "person_shot_without_character", f"{base}.character.present", "Person showcase/eating shots require character.present=true.")
        for field in ("identity", "position", "gaze"):
            if not has_text(character.get(field)):
                add_issue(issues, "ERROR", "missing_character_field", f"{base}.character.{field}", "Character field is required for person shots.")
        if not flatten_text(character.get("micro_expressions")):
            add_issue(issues, "ERROR", "missing_micro_expression", f"{base}.character.micro_expressions", "Describe observable micro-expressions.")
        for field in ("start", "end"):
            if not has_text(emotion.get(field)):
                add_issue(issues, "ERROR", "missing_emotion", f"{base}.emotion.{field}", "Emotion start and end are required for person shots.")
        if not flatten_text(emotion.get("progression")):
            add_issue(issues, "ERROR", "missing_emotion_progression", f"{base}.emotion.progression", "Describe the emotional transition, not only a label.")

    beats = shot.get("action_beats")
    if not isinstance(beats, list) or not beats:
        add_issue(issues, "ERROR", "missing_action_beats", f"{base}.action_beats", "Add at least one timed action beat.")
    elif isinstance(duration, (int, float)):
        previous_start = -1.0
        for beat_index, beat in enumerate(beats):
            beat_path = f"{base}.action_beats[{beat_index}]"
            if not isinstance(beat, dict):
                add_issue(issues, "ERROR", "invalid_action_beat", beat_path, "Action beat must be an object.")
                continue
            beat_start, beat_end = beat.get("start"), beat.get("end")
            if not isinstance(beat_start, (int, float)) or not isinstance(beat_end, (int, float)):
                add_issue(issues, "ERROR", "invalid_action_time", beat_path, "Action beat start/end must be numeric.")
                continue
            if beat_start < 0 or beat_end <= beat_start or beat_end > duration + 0.02:
                add_issue(issues, "ERROR", "action_out_of_range", beat_path, "Action beat must fit inside the shot duration.")
            if beat_start < previous_start:
                add_issue(issues, "ERROR", "action_not_ordered", beat_path, "Action beats must be sorted by start time.")
            previous_start = beat_start
            for field in ("actor", "action", "expression", "product_change", "camera_response"):
                if not has_text(beat.get(field)):
                    add_issue(issues, "ERROR", "missing_action_field", f"{beat_path}.{field}", "Action beat field is required.")

    product_state = shot.get("product_state") or {}
    if product_state.get("profile") != project.get("product_profile"):
        add_issue(issues, "ERROR", "shot_product_profile_mismatch", f"{base}.product_state.profile", "Shot product profile must match project.json.")
    state = product_state.get("state")
    if not has_text(state) or state not in state_profiles:
        add_issue(issues, "ERROR", "unknown_product_state", f"{base}.product_state.state", f"Use a state defined in product_bible.json: {sorted(state_profiles)}")
    packaging = product_state.get("packaging")
    if project_rules.get("packaging_visible") is False and packaging not in (None, False, "none", "hidden"):
        add_issue(issues, "ERROR", "packaging_conflict", f"{base}.product_state.packaging", "Project forbids visible packaging.")
    if project_rules.get("packaging_visible") is False:
        packaging_texts = [
            str(scene.get("location", "")),
            *flatten_text(scene.get("background")),
            *flatten_text(scene.get("foreground")),
            str(shot.get("purpose", "")),
        ]
        for beat in as_list(shot.get("action_beats")):
            if isinstance(beat, dict):
                packaging_texts.extend(
                    str(beat.get(field, ""))
                    for field in ("action", "expression", "product_change", "camera_response")
                )
        packaging_positive = ("包装", "纸盒", "纸箱", "包装袋", "独立包装", "box", "package", "packaging")
        packaging_negative = (
            "无包装", "不要包装", "不出现包装", "禁止包装", "不含包装", "不含独立包装",
            "packaging none", "no packaging", "without packaging",
        )
        for text in packaging_texts:
            if contains_positive_without_negative(text, packaging_positive, packaging_negative):
                add_issue(
                    issues,
                    "ERROR",
                    "packaging_text_conflict",
                    f"{base}.scene_or_action_text",
                    f"Project forbids visible packaging, but shot text mentions packaging: {text}",
                )
                break

    camera = shot.get("camera") or {}
    for field in ("shot_size", "angle", "movement", "focus", "lens_feel"):
        if not has_text(camera.get(field)):
            add_issue(issues, "ERROR", "missing_camera_field", f"{base}.camera.{field}", "Camera field is required.")

    lighting = shot.get("lighting") or {}
    if not has_text(lighting.get("source")) or not has_text(lighting.get("temperature")):
        add_issue(issues, "ERROR", "missing_lighting", f"{base}.lighting", "Lighting source and temperature are required.")

    audio = shot.get("audio") or {}
    delivery_mode = audio.get("delivery_mode")
    if delivery_mode not in VALID_DELIVERY_MODES:
        add_issue(issues, "ERROR", "invalid_delivery_mode", f"{base}.audio.delivery_mode", f"Use one of {sorted(VALID_DELIVERY_MODES)}.")
    if not has_text(audio.get("delivery_rationale")):
        add_issue(issues, "ERROR", "missing_delivery_rationale", f"{base}.audio.delivery_rationale", "Explain how the subtitle script and source style determined this mode.")
    if delivery_mode in {"voiceover", "on_screen_speech"} and not has_text(audio.get("script_text")):
        add_issue(issues, "ERROR", "missing_script_text", f"{base}.audio.script_text", "Spoken shots require the exact assigned subtitle text.")
    if delivery_mode in {"voiceover", "on_screen_speech"} and not has_text(audio.get("voice_direction")):
        add_issue(issues, "ERROR", "missing_voice_direction", f"{base}.audio.voice_direction", "Describe tone, emphasis and pauses.")
    if delivery_mode == "voiceover" and project_rules.get("allow_voiceover") is False:
        add_issue(issues, "ERROR", "project_voiceover_conflict", f"{base}.audio.delivery_mode", "Project disables voice-over.")
    if delivery_mode == "on_screen_speech":
        if project_rules.get("allow_on_screen_speech") is False:
            add_issue(issues, "ERROR", "project_speech_conflict", f"{base}.audio.delivery_mode", "Project disables on-screen speech.")
        if visual_type == "product_showcase":
            add_issue(issues, "ERROR", "speech_without_visible_person", f"{base}.audio.delivery_mode", "A product-only shot cannot contain on-screen speech.")
        if not has_text(audio.get("speech_timing")):
            add_issue(issues, "ERROR", "missing_speech_timing", f"{base}.audio.speech_timing", "Define exactly when the person speaks.")
    if visual_type == "person_eating" and delivery_mode == "on_screen_speech":
        timing = str(audio.get("speech_timing", ""))
        safe_terms = ("咬前", "吞咽后", "咀嚼结束", "吃前", "吃完后", "before biting", "after swallowing")
        if not any(term in timing for term in safe_terms):
            add_issue(issues, "ERROR", "speech_while_eating_risk", f"{base}.audio.speech_timing", "Eating shots may speak only before biting or after chewing/swallowing; never while chewing.")

    if delivery_mode in {"voiceover", "on_screen_speech"}:
        capacity = audio.get("speech_capacity") or {}
        required_capacity = ("segment_count", "effective_characters", "speakable_seconds", "characters_per_second")
        if any(capacity.get(field) is None for field in required_capacity):
            add_issue(issues, "ERROR", "pacing_fields_missing", f"{base}.audio.speech_capacity", "Store segment_count, effective_characters, speakable_seconds and characters_per_second for every spoken shot.")
        else:
            segment_count = capacity.get("segment_count")
            effective_chars = capacity.get("effective_characters")
            speakable_seconds = capacity.get("speakable_seconds")
            stated_rate = capacity.get("characters_per_second")
            if not isinstance(segment_count, int) or segment_count < 1:
                add_issue(issues, "ERROR", "pacing_fields_missing", f"{base}.audio.speech_capacity.segment_count", "segment_count must be a positive integer.")
            elif segment_count > 3:
                add_issue(issues, "ERROR", "script_segment_overload", f"{base}.audio.speech_capacity.segment_count", "A shot may contain at most three spoken segments; split by semantics and action loop.")
            if not isinstance(effective_chars, int) or effective_chars < 1:
                add_issue(issues, "ERROR", "pacing_fields_missing", f"{base}.audio.speech_capacity.effective_characters", "effective_characters must be a positive integer.")
            elif effective_chars != spoken_char_count(str(audio.get("script_text", ""))):
                add_issue(issues, "ERROR", "speech_capacity_mismatch", f"{base}.audio.speech_capacity.effective_characters", "Effective character count must equal Han/letter/digit characters in script_text.")
            if not isinstance(speakable_seconds, (int, float)) or speakable_seconds <= 0:
                add_issue(issues, "ERROR", "speech_window_invalid", f"{base}.audio.speech_capacity.speakable_seconds", "speakable_seconds must be positive and exclude biting, chewing, swallowing, required breaths, pure foley and silent observation.")
            elif isinstance(effective_chars, int) and isinstance(stated_rate, (int, float)):
                calculated_rate = effective_chars / float(speakable_seconds)
                if abs(float(stated_rate) - calculated_rate) > 0.06:
                    add_issue(issues, "ERROR", "speech_capacity_mismatch", f"{base}.audio.speech_capacity.characters_per_second", f"Stored rate {stated_rate} does not match {effective_chars}/{speakable_seconds}={calculated_rate:.2f}.")
                pacing = story.get("pacing") or {}
                limit_field = "maximum_on_screen_chars_per_second" if delivery_mode == "on_screen_speech" else "maximum_voiceover_chars_per_second"
                limit = pacing.get(limit_field)
                if isinstance(limit, (int, float)) and calculated_rate > float(limit) + 0.01:
                    add_issue(issues, "ERROR", "speech_rate_exceeded", f"{base}.audio.speech_capacity.characters_per_second", f"Speech rate {calculated_rate:.2f} chars/s exceeds planned limit {float(limit):.2f}; split or extend instead of accelerating.")

    for field in ("hard_constraints", "prohibited", "continuity"):
        if not flatten_text(shot.get(field)):
            level = "ERROR" if field in {"hard_constraints", "prohibited"} else "WARN"
            add_issue(issues, level, f"missing_{field}", f"{base}.{field}", f"{field} must not be empty.")

    assets = shot.get("asset_links") or {}
    source_frame = resolve_path(project_dir, assets.get("source_first_frame"))
    if source_frame is None:
        add_issue(issues, "ERROR", "missing_source_first_frame", f"{base}.asset_links.source_first_frame", "Extract the exact first temporal frame of every shot.")
    elif not source_frame.exists():
        add_issue(issues, "WARN", "source_first_frame_unavailable", f"{base}.asset_links.source_first_frame", f"File is not accessible: {source_frame}")
    beauty_frame = resolve_path(project_dir, assets.get("selected_beauty_keyframe"))
    if beauty_frame is None:
        add_issue(issues, "WARN", "missing_beauty_keyframe", f"{base}.asset_links.selected_beauty_keyframe", "Select a separate beauty keyframe for visual reference; do not substitute it for the shot's first frame.")
    elif not beauty_frame.exists():
        add_issue(issues, "WARN", "beauty_keyframe_unavailable", f"{base}.asset_links.selected_beauty_keyframe", f"File is not accessible: {beauty_frame}")
    if project.get("generation_mode") == "image_to_video":
        first_frame = resolve_path(project_dir, assets.get("approved_generation_first_frame"))
        if first_frame is None:
            add_issue(issues, "WARN", "missing_approved_generation_first_frame", f"{base}.asset_links.approved_generation_first_frame", "Image-to-video generation should use the approved edit derived from the shot's exact first frame.")
        elif not first_frame.exists():
            add_issue(issues, "WARN", "approved_generation_first_frame_unavailable", f"{base}.asset_links.approved_generation_first_frame", f"File is not accessible: {first_frame}")
    if not as_list(assets.get("product_references")):
        add_issue(issues, "WARN", "missing_product_references", f"{base}.asset_links.product_references", "Bind approved product references.")

    risk = shot.get("risk") or {}
    if risk.get("level") not in VALID_RISKS:
        add_issue(issues, "ERROR", "invalid_risk", f"{base}.risk.level", f"Risk must be one of {sorted(VALID_RISKS)}.")
    if risk.get("level") == "high" and not flatten_text(risk.get("reasons")):
        add_issue(issues, "ERROR", "missing_risk_reason", f"{base}.risk.reasons", "High-risk shots require reasons.")

    style_subtitles = (style.get("subtitle_policy") or {}).get("generate")
    if project_rules.get("subtitles_generated_by_model") is False and style_subtitles is True:
        add_issue(issues, "ERROR", "subtitle_policy_conflict", "library/style_bible.json.subtitle_policy.generate", "Project forbids model-generated subtitles.")


def determine_release_status(project: Dict[str, Any], issues: Sequence[Dict[str, Any]]) -> str:
    if any(issue["level"] in {"ERROR", "BLOCK"} for issue in issues):
        return "NOT CLEARED FOR RELEASE"
    intended_use = (project.get("commercial") or {}).get("intended_use", "internal_test")
    if intended_use == "commercial_release":
        return "CLEARED FOR RELEASE"
    if intended_use == "client_review":
        return "CLIENT REVIEW ONLY"
    return "INTERNAL TEST ONLY"


def lint_project(project_dir: Path, write_report: bool = True) -> Dict[str, Any]:
    project_dir = project_dir.expanduser().resolve()
    issues: List[Dict[str, Any]] = []
    if not project_dir.is_dir():
        add_issue(issues, "ERROR", "missing_project_directory", str(project_dir), "Project directory does not exist.")
        project: Dict[str, Any] = {}
    elif not validate_required_files(project_dir, issues):
        project = {}
    else:
        bundle = read_bundle(project_dir)
        project = bundle["project"]
        validate_project(project_dir, bundle, issues)
        reuse_audit = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "audit_asset_reuse.py"),
                "--plan",
                str(project_dir / REQUIRED_FILES["asset_reuse"]),
                "--stage",
                "pre-generation",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if reuse_audit.returncode != 0:
            add_issue(
                issues,
                "ERROR",
                "asset_reuse_audit_blocked",
                str(REQUIRED_FILES["asset_reuse"]),
                (reuse_audit.stdout or reuse_audit.stderr).strip(),
            )

    counts = {level: sum(issue["level"] == level for issue in issues) for level in ("ERROR", "BLOCK", "WARN")}
    report = {
        "schema_version": "1.0",
        "project_dir": str(project_dir),
        "generated_at": now_iso(),
        "counts": counts,
        "release_status": determine_release_status(project, issues),
        "issues": issues,
    }
    if write_report and project_dir.is_dir():
        write_json(project_dir / "review" / "lint_report.json", report)
    return report


def applicable_corrections(bundle: Dict[str, Dict[str, Any]], shot: Dict[str, Any]) -> List[Dict[str, Any]]:
    project = bundle["project"]
    rules = bundle["corrections"].get("rules") or []
    matching: List[Dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("active") is not True:
            continue
        scope, target = rule.get("scope"), rule.get("target")
        expected = {
            "shot": shot.get("id"),
            "project": project.get("project_id"),
            "product": project.get("product_profile"),
            "style": project.get("style_profile"),
        }.get(scope)
        if target in (None, "*", expected):
            matching.append(rule)
    return sorted(
        matching,
        key=lambda item: (int(item.get("priority", 0)), item.get("updated_at", "")),
        reverse=True,
    )


def applicable_knowledge(bundle: Dict[str, Dict[str, Any]], shot: Dict[str, Any]) -> List[Dict[str, Any]]:
    context = {
        "visual_type": shot.get("visual_type"),
        "product_state": (shot.get("product_state") or {}).get("state"),
        "delivery_mode": (shot.get("audio") or {}).get("delivery_mode"),
        "narrative_role": shot.get("narrative_role"),
    }
    matches = []
    for entry in bundle["knowledge"].get("entries") or []:
        if not isinstance(entry, dict) or entry.get("approved") is not True:
            continue
        applies_to = entry.get("applies_to") or {}
        if all(
            expected in (None, "*", context.get(key)) or context.get(key) in as_list(expected)
            for key, expected in applies_to.items()
            if key in context
        ):
            matches.append(entry)
    return sorted(matches, key=lambda item: int(item.get("priority", 0)), reverse=True)


def action_text(beats: Iterable[Dict[str, Any]]) -> str:
    lines = []
    for beat in beats:
        lines.append(
            f"{beat.get('start', 0):.2f}–{beat.get('end', 0):.2f}秒："
            f"{beat.get('actor')}执行“{beat.get('action')}”；"
            f"表情为“{beat.get('expression')}”；"
            f"产品变化为“{beat.get('product_change')}”；"
            f"镜头配合为“{beat.get('camera_response')}”。"
        )
    return "\n".join(lines)


def compile_shot(bundle: Dict[str, Dict[str, Any]], shot: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    project = bundle["project"]
    product = bundle["product"]
    style = bundle["style"]
    state_id = (shot.get("product_state") or {}).get("state")
    state = (product.get("state_profiles") or {}).get(state_id, {})
    corrections = applicable_corrections(bundle, shot)
    correction_text = [rule.get("instruction", "") for rule in corrections if has_text(rule.get("instruction"))]
    knowledge = applicable_knowledge(bundle, shot)
    knowledge_instructions = [entry.get("instruction", "") for entry in knowledge if entry.get("type") in {"prompt", "rule"} and has_text(entry.get("instruction"))]
    knowledge_images = [entry.get("path") for entry in knowledge if entry.get("type") == "image" and has_text(entry.get("path"))]

    product_traits = flatten_text(product.get("immutable_traits"))
    product_traits.extend(flatten_text(state.get("required")))
    product_traits.extend(flatten_text((shot.get("product_state") or {}).get("shot_specific_traits")))

    prohibited = flatten_text(product.get("global_negative_constraints"))
    prohibited.extend(flatten_text(state.get("forbidden")))
    prohibited.extend(flatten_text(shot.get("prohibited")))
    prohibited = list(dict.fromkeys(prohibited))

    scene = shot.get("scene") or {}
    character = shot.get("character") or {}
    emotion = shot.get("emotion") or {}
    camera = shot.get("camera") or {}
    lighting = shot.get("lighting") or {}
    audio = shot.get("audio") or {}
    timecode = shot.get("timecode") or {}
    product_state = shot.get("product_state") or {}

    visual_type = shot.get("visual_type")
    if visual_type == "product_showcase":
        visual_instruction = "本镜头只展示产品与产品所处场景，不出现人物、手、脸或人物动作。"
        character_instruction = ""
    else:
        visual_label = "人物展示产品" if visual_type == "person_product_showcase" else "人物吃产品"
        visual_instruction = f"本镜头画面类型严格限定为“{visual_label}”，不得扩写为开箱、包装、烹饪、直播、电商界面或无关情节。"
        character_instruction = (
            f"人物为{character.get('identity')}，位于{character.get('position')}，视线为{character.get('gaze')}。"
            f"微表情必须呈现：{join_cn(character.get('micro_expressions'))}。"
            f"情绪从“{emotion.get('start')}”开始，经过“{join_cn(emotion.get('progression'))}”，最终到达“{emotion.get('end')}”，"
            f"表演强度为{emotion.get('intensity', 'natural')}，通过眼神、眉毛、嘴角、停顿和身体动作表现。"
        )

    delivery_mode = audio.get("delivery_mode")
    if delivery_mode == "on_screen_speech":
        audio_instruction = (
            f"人物在镜头内说出：“{audio.get('script_text')}”，只在“{audio.get('speech_timing')}”讲话并自然对口型。"
            f"若同时吃产品，只能在咬前或咀嚼吞咽结束后讲话，禁止边咀嚼边说。声音指导：{audio.get('voice_direction')}。"
        )
    elif delivery_mode == "voiceover":
        audio_instruction = (
            f"字幕稿在本镜头作为后期画外音：“{audio.get('script_text')}”。人物不说话、不做口播口型；"
            f"嘴部只执行展示表情、咬下、闭口咀嚼或吞咽动作。声音指导：{audio.get('voice_direction')}。"
        )
    else:
        audio_instruction = "本镜头无口播和画外音；只保留与动作一致的自然拟音和环境声。"

    prompt_body = f"""生成一段约{timecode.get('duration')}秒、{project.get('aspect_ratio')}比例的{style.get('name')}视频片段，镜头编号{shot.get('id')}，叙事职责是“{shot.get('narrative_role')}”，用途是“{shot.get('purpose')}”。{visual_instruction}使用真实、连续、可用于商业审核的画面，不生成未获授权的额外内容。

保持原片：{join_cn(shot.get('source_locks'))}。只允许修改：{join_cn(shot.get('allowed_changes'))}。原片可确认事实：{join_cn(shot.get('source_facts'))}。

场景位于{scene.get('location')}。选择该场景的理由是：{shot.get('scene_rationale')}。背景包括：{join_cn(scene.get('background'))}。前景包括：{join_cn(scene.get('foreground'))}。保持前景、人物、产品和背景之间真实的遮挡与空间层次。

{character_instruction}

严格按照以下镜头内时间轴执行动作：
{action_text(shot.get('action_beats') or [])}

产品使用“{product.get('name')}”规范，当前主要状态为“{state_id}”：{state.get('description', '')}。数量为{product_state.get('count')}，包装状态为{product_state.get('packaging')}。产品必须满足：{join_cn(product_traits)}。

摄影采用{camera.get('shot_size')}，机位为{camera.get('angle')}，运镜为{camera.get('movement')}，焦点为{camera.get('focus')}，镜头质感为{camera.get('lens_feel')}。灯光来源为{lighting.get('source')}，色温为{lighting.get('temperature')}，补充要求：{join_cn(lighting.get('notes'))}。

声音方式为“{delivery_mode}”，依据是：{audio.get('delivery_rationale')}。{audio_instruction}保留或生成的拟音包括：{join_cn(audio.get('foley'))}。音乐要求：{audio.get('music')}。

连续性：{join_cn(shot.get('continuity'))}。当前镜头硬约束：{join_cn(shot.get('hard_constraints'))}。活跃纠错规则：{join_cn(correction_text, '无新增纠错规则')}。按本镜头条件命中的知识库规则：{join_cn(knowledge_instructions, '无')}。

禁止出现：{join_cn(prohibited)}。除上述允许修改项外，不改变人物身份、构图、产品数量、动作顺序、光线、场景、镜头角度或前后镜头连续性。"""

    metadata = {
        "shot_id": shot.get("id"),
        "title": shot.get("title"),
        "timecode": timecode,
        "visual_type": visual_type,
        "narrative_role": shot.get("narrative_role"),
        "delivery_mode": delivery_mode,
        "script_segment_ids": as_list(shot.get("script_segment_ids")),
        "risk": shot.get("risk"),
        "product_profile": product.get("profile_id"),
        "product_version": product.get("version"),
        "style_profile": style.get("profile_id"),
        "style_version": style.get("version"),
        "correction_rule_ids": [rule.get("id") for rule in corrections],
        "source_first_frame": (shot.get("asset_links") or {}).get("source_first_frame"),
        "selected_beauty_keyframe": (shot.get("asset_links") or {}).get("selected_beauty_keyframe"),
        "approved_generation_first_frame": (shot.get("asset_links") or {}).get("approved_generation_first_frame"),
        "product_references": as_list((shot.get("asset_links") or {}).get("product_references")),
        "knowledge_entry_ids": [entry.get("id") for entry in knowledge],
        "knowledge_image_references": knowledge_images,
        "prompt_file": f"prompts/{shot.get('id')}.md",
    }
    markdown = f"""# {shot.get('id')}｜{shot.get('title')}

- 时间：{timecode.get('start')}–{timecode.get('end')} 秒；独立生成时长 {timecode.get('duration')} 秒
- 风险：{(shot.get('risk') or {}).get('level')} — {join_cn((shot.get('risk') or {}).get('reasons'))}
- 产品规范：{product.get('profile_id')} v{product.get('version')}
- 风格规范：{style.get('profile_id')} v{style.get('version')}
- 纠错规则：{join_cn(metadata['correction_rule_ids'], '无')}

## 可直接提交的完整 Prompt

```text
{prompt_body}
```
"""
    return markdown, metadata


def build_shot_cards(bundle: Dict[str, Dict[str, Any]], report: Dict[str, Any]) -> str:
    project = bundle["project"]
    shots = bundle["shots"].get("shots") or []
    total_duration = sum(float((shot.get("timecode") or {}).get("duration", 0) or 0) for shot in shots)
    visual_seconds = {key: 0.0 for key in VALID_VISUAL_TYPES}
    delivery_seconds = {key: 0.0 for key in VALID_DELIVERY_MODES}
    for shot in shots:
        duration = float((shot.get("timecode") or {}).get("duration", 0) or 0)
        if shot.get("visual_type") in visual_seconds:
            visual_seconds[shot.get("visual_type")] += duration
        delivery_mode = (shot.get("audio") or {}).get("delivery_mode")
        if delivery_mode in delivery_seconds:
            delivery_seconds[delivery_mode] += duration

    ratio = lambda seconds: f"{seconds / total_duration:.0%}" if total_duration else "0%"
    lines = [
        f"# {project.get('project_name')}｜分镜确认卡",
        "",
        f"- 项目：`{project.get('project_id')}`",
        f"- 平台：{project.get('platform')}；模式：{project.get('generation_mode')}；比例：{project.get('aspect_ratio')}",
        f"- 发布状态：**{report.get('release_status')}**",
        f"- 检查：ERROR {report['counts']['ERROR']} / BLOCK {report['counts']['BLOCK']} / WARN {report['counts']['WARN']}",
        f"- 画面占比：产品展示 {ratio(visual_seconds['product_showcase'])} / 人物展示产品 {ratio(visual_seconds['person_product_showcase'])} / 人物吃产品 {ratio(visual_seconds['person_eating'])}",
        f"- 声音占比：画外音 {ratio(delivery_seconds['voiceover'])} / 人物讲话 {ratio(delivery_seconds['on_screen_speech'])} / 无口播 {ratio(delivery_seconds['silent'])}",
        "",
        "| 镜头 | 时间 | 画面类型 | 叙事职责 | 声音 | 情绪变化 | 产品状态 | 首帧/美观帧 |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for shot in shots:
        timecode = shot.get("timecode") or {}
        emotion = shot.get("emotion") or {}
        product_state = shot.get("product_state") or {}
        risk = shot.get("risk") or {}
        assets = shot.get("asset_links") or {}
        frame_status = f"{'已提取' if assets.get('source_first_frame') else '缺首帧'} / {'已选' if assets.get('selected_beauty_keyframe') else '待选'}"
        emotion_summary = "不适用" if shot.get("visual_type") == "product_showcase" else f"{emotion.get('start')} → {join_cn(emotion.get('progression'), '')} → {emotion.get('end')}"
        lines.append(
            "| {id} | {duration}s | {visual} | {role} | {delivery} | {emotion} | {state} | {frames} |".format(
                id=table_escape(shot.get("id")),
                duration=table_escape(timecode.get("duration")),
                visual=table_escape(shot.get("visual_type")),
                role=table_escape(shot.get("narrative_role")),
                delivery=table_escape((shot.get("audio") or {}).get("delivery_mode")),
                emotion=table_escape(emotion_summary),
                state=table_escape(product_state.get("state")),
                frames=frame_status,
            )
        )

    high_risk = [shot for shot in shots if (shot.get("risk") or {}).get("level") == "high"]
    lines.extend(["", "## 建议生成顺序", ""])
    if high_risk:
        first = high_risk[0]
        lines.append(f"先测试 `{first.get('id')}`：{join_cn((first.get('risk') or {}).get('reasons'))}。该镜头通过后再批量生成。")
    else:
        lines.append("未标记高风险镜头；仍建议先测试一个最能代表人物和产品质感的镜头。")

    blockers = [issue for issue in report.get("issues", []) if issue.get("level") in {"ERROR", "BLOCK"}]
    lines.extend(["", "## 必须处理", ""])
    if blockers:
        for issue in blockers:
            lines.append(f"- [{issue['level']}] `{issue['code']}` {issue['path']}：{issue['message']}")
    else:
        lines.append("- 没有结构错误或商业阻断项。")
    return "\n".join(lines) + "\n"


def compile_project(project_dir: Path) -> Dict[str, Any]:
    project_dir = project_dir.expanduser().resolve()
    report = lint_project(project_dir, write_report=True)
    if report["counts"]["ERROR"]:
        raise RuntimeError(f"Compilation blocked by {report['counts']['ERROR']} structural error(s). Review review/lint_report.json.")
    bundle = read_bundle(project_dir)
    intended_use = (bundle["project"].get("commercial") or {}).get("intended_use", "internal_test")
    if intended_use == "commercial_release" and report["counts"]["BLOCK"]:
        raise RuntimeError(
            f"Compilation blocked for commercial_release by {report['counts']['BLOCK']} commercial clearance block(s). "
            "Set intended_use to internal_test/client_review while preparing, or clear the commercial gate first."
        )

    prompt_dir = project_dir / "prompts"
    compile_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    history_dir = prompt_dir / "history" / compile_id
    entries = []
    for shot in bundle["shots"].get("shots") or []:
        markdown, metadata = compile_shot(bundle, shot)
        prompt_path = prompt_dir / f"{shot.get('id')}.md"
        write_text(prompt_path, markdown)
        write_text(history_dir / f"{shot.get('id')}.md", markdown)
        prompt_match = re.search(r"```text\s*\n(.*?)\n```", markdown, re.S)
        if not prompt_match:
            raise RuntimeError(f"Compiled prompt for {shot.get('id')} has no canonical text block.")
        metadata["prompt_sha256"] = sha256_text(prompt_match.group(1).strip())
        metadata["prompt_file_sha256"] = sha256_file(prompt_path)
        entries.append(metadata)

    source = bundle["source"]
    generation_pack = {
        "schema_version": "1.0",
        "project_id": bundle["project"].get("project_id"),
        "compile_id": compile_id,
        "generated_at": now_iso(),
        "source_sha256": source.get("sha256"),
        "release_status": report.get("release_status"),
        "history_dir": str(history_dir.relative_to(project_dir)),
        "shots": entries,
    }
    write_json(prompt_dir / "generation_pack.json", generation_pack)
    write_json(history_dir / "generation_pack.json", generation_pack)
    input_snapshot = {
        "schema_version": "1.0",
        "compile_id": compile_id,
        "captured_at": now_iso(),
        "project": bundle["project"],
        "product_bible": bundle["product"],
        "product_library": bundle["product_library"],
        "style_bible": bundle["style"],
        "correction_memory": bundle["corrections"],
        "knowledge_index": bundle["knowledge"],
        "avatar_library": bundle["avatars"],
        "story_plan": bundle["story"],
        "asset_reuse_plan": bundle["asset_reuse"],
        "source_manifest": bundle["source"],
        "shot_manifest": bundle["shots"],
    }
    write_json(history_dir / "input_snapshot.json", input_snapshot)
    shot_cards = build_shot_cards(bundle, report)
    write_text(project_dir / "review" / "shot_cards.md", shot_cards)
    write_text(history_dir / "shot_cards.md", shot_cards)

    project = bundle["project"]
    if project.get("status") in {"draft", "analyzed"}:
        project["status"] = "prompt_ready"
    project["updated_at"] = now_iso()
    write_json(project_dir / "project.json", project)
    workflow_path = project_dir / "planning" / "workflow_state.json"
    if workflow_path.is_file():
        workflow = load_json(workflow_path)
        workflow["current_stage"] = "prompt_compile"
        workflow["status"] = "in_progress"
        workflow["blocked_by"] = []
        workflow["next_allowed_actions"] = ["export_aligned_txt", "export_docx", "run_alignment_check"]
        completed = workflow.setdefault("completed_stages", [])
        if "first_frame_approval" not in completed:
            completed.append("first_frame_approval")
        workflow["updated_at"] = now_iso()
        write_json(workflow_path, workflow)
    return {
        "shot_count": len(entries),
        "release_status": report.get("release_status"),
        "compile_id": compile_id,
        "history_dir": str(history_dir),
        "generation_pack": str(prompt_dir / "generation_pack.json"),
        "shot_cards": str(project_dir / "review" / "shot_cards.md"),
    }


def print_lint(report: Dict[str, Any]) -> None:
    print(f"Release status: {report['release_status']}")
    print(f"ERROR={report['counts']['ERROR']} BLOCK={report['counts']['BLOCK']} WARN={report['counts']['WARN']}")
    for issue in report.get("issues", []):
        print(f"[{issue['level']}] {issue['code']} {issue['path']}: {issue['message']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate a Jimeng video-remix project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint", help="Validate project completeness, conflicts and release clearance.")
    lint_parser.add_argument("--project-dir", required=True, type=Path)

    compile_parser = subparsers.add_parser("compile", help="Compile prompts, generation pack and review cards.")
    compile_parser.add_argument("--project-dir", required=True, type=Path)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "lint":
            report = lint_project(args.project_dir, write_report=True)
            print_lint(report)
            return 1 if report["counts"]["ERROR"] or report["counts"]["BLOCK"] else 0
        if args.command == "compile":
            print(json.dumps(compile_project(args.project_dir), ensure_ascii=False, indent=2))
            return 0
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
