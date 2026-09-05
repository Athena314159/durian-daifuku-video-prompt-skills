#!/usr/bin/env python3
"""Validate versioned full-delivery image/text handoffs against one semantic lock."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


SHA256_RE = re.compile(r"[0-9a-f]{64}")
SHOT_RE = re.compile(r"S\d+")
SOURCE_RE = re.compile(r"SRC\d+")
INSERTED_RE = re.compile(r"ADD\d+")
UNIT_RE = re.compile(r"(?:SRC|ADD)\d+")
SHOT_MAP_HASH_FIELDS = (
    "source_duration_seconds",
    "source_units",
    "inserted_units",
    "generation_shot_map",
    "eating_plan",
    "break_plan",
)
LAYER_KEYS = (
    "emotion_trigger",
    "gaze",
    "facial_microreaction",
    "body_hand_preparation",
    "breath_pause",
    "voice_speech",
)
SOURCE_CARD_FIELDS = (
    "shot_id",
    "source_shot_id",
    "source_timecode",
    "generation_timecode",
    "storyboard_description",
    "script_text",
    "source_performance_layers",
    "packaging_evidence",
)
INSERTED_CARD_FIELDS = (
    "shot_id",
    "inserted_shot_id",
    "generation_timecode",
    "storyboard_description",
    "script_text",
    "insertion_rationale",
    "rhythm_anchor",
    "source_reference_shot_ids",
    "source_reference_frame",
    "source_performance_layers",
    "packaging_evidence",
)
FORBIDDEN_ALIGNMENT_KEYS = {
    "alignment_table",
    "alignment_rows",
    "alignment_markdown",
    "natural_language_alignment_table",
}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def semantic_shot_map_sha256(value: dict[str, Any]) -> str:
    """Hash only the locked semantic payload, identically on both branches."""
    payload = {field: value.get(field) for field in SHOT_MAP_HASH_FIELDS}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_timecode(value: Any, path: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{path} must be a start/end/duration object")
        return False
    if set(value) != {"start", "end", "duration"}:
        errors.append(f"{path} must contain exactly start, end, duration")
        return False
    start, end, duration = value.get("start"), value.get("end"), value.get("duration")
    if not all(numeric(item) for item in (start, end, duration)):
        errors.append(f"{path} start/end/duration must be numeric")
        return False
    if not (end > start >= 0 and duration > 0):
        errors.append(f"{path} must satisfy end > start >= 0 and duration > 0")
        return False
    if abs((end - start) - duration) > 0.001:
        errors.append(f"{path} duration must equal end-start within 0.001 seconds")
        return False
    return True


def validate_id_array(
    value: Any,
    path: str,
    pattern: re.Pattern[str],
    errors: list[str],
    *,
    allow_empty: bool = True,
) -> bool:
    if not isinstance(value, list) or (not allow_empty and not value):
        errors.append(f"{path} must be {'a non-empty' if not allow_empty else 'an'} ID array")
        return False
    if any(not isinstance(item, str) or not pattern.fullmatch(item) for item in value):
        errors.append(f"{path} contains an invalid ID")
        return False
    if len(value) != len(set(value)):
        errors.append(f"{path} contains duplicate IDs")
        return False
    return True


def ordered_subset(values: list[str], expected: list[str]) -> bool:
    iterator = iter(expected)
    return all(any(candidate == value for candidate in iterator) for value in values)


def check_absolute_file_asset(asset: Any, path: str, errors: list[str], *, image: bool) -> tuple[str, str, str] | None:
    if not isinstance(asset, dict):
        errors.append(f"{path} must be an asset object")
        return None
    asset_id = asset.get("asset_id")
    path_key = "image_path" if image else "path"
    raw_path = asset.get(path_key)
    declared_hash = asset.get("sha256")
    if not nonempty(asset_id):
        errors.append(f"{path}.asset_id missing")
    if not nonempty(raw_path):
        errors.append(f"{path}.{path_key} missing")
        resolved = None
    else:
        resolved = Path(raw_path).expanduser()
        if not resolved.is_absolute():
            errors.append(f"{path}.{path_key} must be an absolute path")
        elif not resolved.exists() or not resolved.is_file():
            errors.append(f"{path}.{path_key} does not exist as a file")
    if not isinstance(declared_hash, str) or not SHA256_RE.fullmatch(declared_hash):
        errors.append(f"{path}.sha256 must be a lowercase 64-character SHA-256")
    elif resolved is not None and resolved.is_absolute() and resolved.is_file():
        actual = file_sha256(resolved)
        if actual != declared_hash:
            errors.append(f"{path}.sha256 does not match the file bytes")
    if not (nonempty(asset_id) and nonempty(raw_path) and isinstance(declared_hash, str)):
        return None
    return str(asset_id), str(raw_path), str(declared_hash)


def validate_performance_layers(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be the complete six-layer evidence object")
        return
    missing = [key for key in LAYER_KEYS if key not in value]
    extra = [key for key in value if key not in LAYER_KEYS]
    if missing:
        errors.append(f"{path} missing six-layer keys: {missing}")
    if extra:
        errors.append(f"{path} has unknown six-layer keys: {extra}")
    for key in LAYER_KEYS:
        layer = value.get(key)
        layer_path = f"{path}.{key}"
        if not isinstance(layer, dict):
            errors.append(f"{layer_path} must be an evidence object")
            continue
        required = {"status", "source_timecode", "source_reference_frame", "observable_evidence", "confidence", "gap_reason"}
        if set(layer) != required:
            errors.append(f"{layer_path} must contain exactly {sorted(required)}")
        status = layer.get("status")
        if status not in {"observed", "audible", "not_visible", "not_applicable", "template_supplement"}:
            errors.append(f"{layer_path}.status invalid")
        if not nonempty(layer.get("observable_evidence")):
            errors.append(f"{layer_path}.observable_evidence missing")
        confidence = layer.get("confidence")
        if not numeric(confidence) or not 0 <= confidence <= 1:
            errors.append(f"{layer_path}.confidence must be within 0..1")
        evidence_tc = layer.get("source_timecode")
        if status in {"observed", "audible"}:
            validate_timecode(evidence_tc, f"{layer_path}.source_timecode", errors)
        elif evidence_tc is not None:
            errors.append(f"{layer_path}.source_timecode must be null for {status}")
        reference = layer.get("source_reference_frame")
        if status == "observed" and not nonempty(reference):
            errors.append(f"{layer_path}.source_reference_frame required for observed evidence")
        elif reference is not None and not nonempty(reference):
            errors.append(f"{layer_path}.source_reference_frame must be non-empty or null")
        gap_reason = layer.get("gap_reason")
        if status == "template_supplement" and not nonempty(gap_reason):
            errors.append(f"{layer_path}.gap_reason required for template_supplement")
        elif gap_reason is not None and not nonempty(gap_reason):
            errors.append(f"{layer_path}.gap_reason must be non-empty or null")


def validate_packaging(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"visible", "visible_faces"}:
        errors.append(f"{path} must contain exactly visible and visible_faces")
        return
    visible = value.get("visible")
    faces = value.get("visible_faces")
    if not isinstance(visible, bool):
        errors.append(f"{path}.visible must be boolean")
    if not isinstance(faces, list):
        errors.append(f"{path}.visible_faces must be an array")
        return
    if visible is True and not faces:
        errors.append(f"{path}.visible=true requires at least one packaging master visible face")
    if visible is False and faces:
        errors.append(f"{path}.visible=false cannot carry visible faces")
    seen_faces: set[tuple[str, str]] = set()
    for index, face in enumerate(faces):
        face_path = f"{path}.visible_faces[{index}]"
        if not isinstance(face, dict):
            errors.append(f"{face_path} must be an object")
            continue
        box_id, face_name = face.get("box_id"), face.get("face")
        if not nonempty(box_id):
            errors.append(f"{face_path}.box_id missing")
        if face_name not in {"front", "side", "top"}:
            errors.append(f"{face_path}.face invalid")
        if nonempty(box_id) and face_name in {"front", "side", "top"}:
            pair = (str(box_id), str(face_name))
            if pair in seen_faces:
                errors.append(f"{face_path} duplicates packaging face {pair}")
            seen_faces.add(pair)
        if face.get("visibility_state") not in {"fully_visible", "partially_visible", "occluded", "off_frame_partial"}:
            errors.append(f"{face_path}.visibility_state invalid")
        check_absolute_file_asset(face.get("master_asset"), f"{face_path}.master_asset", errors, image=False)
        regions = face.get("visible_regions")
        if not isinstance(regions, list) or not regions or any(not nonempty(item) for item in regions):
            errors.append(f"{face_path}.visible_regions must contain observed regions")
        elif len(regions) != len(set(regions)):
            errors.append(f"{face_path}.visible_regions contains duplicates")
        if not nonempty(face.get("observable_evidence")):
            errors.append(f"{face_path}.observable_evidence missing")
        if face.get("qa_status") != "approved":
            errors.append(f"{face_path}.qa_status must equal approved")


def validate_text_unit(unit: Any, kind: str, path: str, errors: list[str]) -> str | None:
    if not isinstance(unit, dict):
        errors.append(f"{path} must be a card object")
        return None
    required = SOURCE_CARD_FIELDS if kind == "source" else INSERTED_CARD_FIELDS
    for field in required:
        if field not in unit:
            errors.append(f"{path}.{field} missing")
    shot_id = unit.get("shot_id")
    if not isinstance(shot_id, str) or not SHOT_RE.fullmatch(shot_id):
        errors.append(f"{path}.shot_id invalid")
    unit_key = "source_shot_id" if kind == "source" else "inserted_shot_id"
    pattern = SOURCE_RE if kind == "source" else INSERTED_RE
    unit_id = unit.get(unit_key)
    if not isinstance(unit_id, str) or not pattern.fullmatch(unit_id):
        errors.append(f"{path}.{unit_key} invalid")
        unit_id = None
    if kind == "source":
        validate_timecode(unit.get("source_timecode"), f"{path}.source_timecode", errors)
        if "inserted_shot_id" in unit:
            errors.append(f"{path} cannot carry inserted_shot_id")
    else:
        if "source_timecode" in unit or "source_shot_id" in unit:
            errors.append(f"{path} ADD cannot carry source_timecode/source_shot_id")
        if not nonempty(unit.get("insertion_rationale")):
            errors.append(f"{path}.insertion_rationale missing")
        if not nonempty(unit.get("rhythm_anchor")):
            errors.append(f"{path}.rhythm_anchor missing")
        validate_id_array(unit.get("source_reference_shot_ids"), f"{path}.source_reference_shot_ids", SOURCE_RE, errors, allow_empty=False)
        if not nonempty(unit.get("source_reference_frame")):
            errors.append(f"{path}.source_reference_frame missing")
    validate_timecode(unit.get("generation_timecode"), f"{path}.generation_timecode", errors)
    if not nonempty(unit.get("storyboard_description")):
        errors.append(f"{path}.storyboard_description missing")
    if not nonempty(unit.get("script_text")):
        errors.append(f"{path}.script_text missing; use explicit silent marker instead of omitting the voice-over field")
    validate_performance_layers(unit.get("source_performance_layers"), f"{path}.source_performance_layers", errors)
    validate_packaging(unit.get("packaging_evidence"), f"{path}.packaging_evidence", errors)
    return str(unit_id) if unit_id is not None else None


def validate_crisp_proof(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} missing")
        return
    for field in ("single_snap", "fracture_visible", "material_conservation_locked"):
        if value.get(field) is not True:
            errors.append(f"{path}.{field} must be true")
    crumbs = value.get("crumbs")
    if not isinstance(crumbs, dict):
        errors.append(f"{path}.crumbs missing")
    else:
        minimum, maximum = crumbs.get("minimum"), crumbs.get("maximum")
        if not (
            isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and isinstance(maximum, int)
            and not isinstance(maximum, bool)
            and 3 <= minimum <= maximum <= 8
        ):
            errors.append(f"{path}.crumbs must satisfy 3 <= minimum <= maximum <= 8")
    for field in (
        "complementary_orange_gold_fracture",
        "same_stick_two_piece_conservation",
        "sound_sync",
        "foley",
    ):
        if not nonempty(value.get(field)):
            errors.append(f"{path}.{field} missing")


def validate_eating_plan(
    value: Any,
    source_duration: float,
    owners: dict[str, str],
    shot_durations: dict[str, float],
    total_generation_duration: float,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append("eating_plan must be an object")
        return
    policy = value.get("policy")
    expected_policy = {
        "source_duration_threshold_seconds": 30,
        "target_event_count": 3,
        "events_are_non_contiguous": True,
        "one_event_is_not_multiple_images": True,
    }
    if policy != expected_policy:
        errors.append("eating_plan.policy must lock 30-second/3-event/non-contiguous semantics")
    occurrences = value.get("occurrences")
    if not isinstance(occurrences, list):
        errors.append("eating_plan.occurrences must be an array")
        return
    seen_ids: set[str] = set()
    seen_groups: set[str] = set()
    seen_units: set[str] = set()
    seen_shots: set[str] = set()
    intervals: list[tuple[float, float, str]] = []
    source_count = 0
    inserted_count = 0
    for index, occurrence in enumerate(occurrences):
        path = f"eating_plan.occurrences[{index}]"
        if not isinstance(occurrence, dict):
            errors.append(f"{path} must be an object")
            continue
        occurrence_id = occurrence.get("id")
        group_id = occurrence.get("event_group_id")
        shot_id = occurrence.get("shot_id")
        unit_id = occurrence.get("unit_id")
        for raw, seen, label in (
            (occurrence_id, seen_ids, "id"),
            (group_id, seen_groups, "event_group_id"),
            (unit_id, seen_units, "unit_id"),
        ):
            if not nonempty(raw) or raw in seen:
                errors.append(f"{path}.{label} missing or duplicate; one eating event cannot be counted as multiple images")
            else:
                seen.add(str(raw))
        if not isinstance(shot_id, str) or not SHOT_RE.fullmatch(shot_id):
            errors.append(f"{path}.shot_id invalid")
        elif shot_id in seen_shots:
            errors.append(f"{path}.shot_id duplicates another eating event; events must be rhythmically non-contiguous")
        else:
            seen_shots.add(shot_id)
        if not isinstance(unit_id, str) or not UNIT_RE.fullmatch(unit_id) or owners.get(unit_id) != shot_id:
            errors.append(f"{path}.unit_id must bind a locked unit owned by shot_id")
        origin = occurrence.get("origin")
        if origin == "source":
            source_count += 1
            if occurrence.get("source_shot_id") != unit_id or not isinstance(unit_id, str) or not SOURCE_RE.fullmatch(unit_id):
                errors.append(f"{path}.source_shot_id must equal the SRC unit_id")
            if "inserted_shot_id" in occurrence:
                errors.append(f"{path} source occurrence cannot carry inserted_shot_id")
        elif origin == "inserted":
            inserted_count += 1
            if occurrence.get("inserted_shot_id") != unit_id or not isinstance(unit_id, str) or not INSERTED_RE.fullmatch(unit_id):
                errors.append(f"{path}.inserted_shot_id must equal the ADD unit_id")
            if "source_shot_id" in occurrence:
                errors.append(f"{path} inserted occurrence cannot carry source_shot_id")
        else:
            errors.append(f"{path}.origin must be source or inserted")
        local_ok = validate_timecode(occurrence.get("generation_timecode"), f"{path}.generation_timecode", errors)
        if local_ok and isinstance(shot_id, str) and shot_id in shot_durations:
            if occurrence["generation_timecode"]["end"] > shot_durations[shot_id] + 0.001:
                errors.append(f"{path}.generation_timecode exceeds its S duration")
        timeline_ok = validate_timecode(occurrence.get("timeline_timecode"), f"{path}.timeline_timecode", errors)
        if timeline_ok:
            start = float(occurrence["timeline_timecode"]["start"])
            end = float(occurrence["timeline_timecode"]["end"])
            if end > total_generation_duration + 0.001:
                errors.append(f"{path}.timeline_timecode exceeds the generated timeline")
            intervals.append((start, end, str(occurrence_id)))
        if not nonempty(occurrence.get("rhythm_anchor")):
            errors.append(f"{path}.rhythm_anchor missing")
        if not nonempty(occurrence.get("script_anchor")):
            errors.append(f"{path}.script_anchor missing")
        phases = occurrence.get("required_phases")
        if not isinstance(phases, list) or "bite" not in phases or "closed_mouth_chew" not in phases:
            errors.append(f"{path}.required_phases must include bite and closed_mouth_chew without forcing swallow")
        if occurrence.get("non_contiguous_event") is not True:
            errors.append(f"{path}.non_contiguous_event must be true")
    intervals.sort()
    for previous, current in zip(intervals, intervals[1:]):
        if current[0] - previous[1] < 0.05:
            errors.append(
                f"eating events {previous[2]} and {current[2]} are continuous/overlapping; "
                "three non-contiguous events are required, not three frames of one event"
            )
    if source_duration >= 30:
        if source_count < 3:
            required_inserted = 3 - source_count
            if len(occurrences) != 3 or inserted_count != required_inserted:
                errors.append(
                    "30-second eating policy must preserve source events and insert only enough distinct events to total 3"
                )
        elif inserted_count != 0 or len(occurrences) != source_count:
            errors.append("source already has at least 3 eating events; eating_plan must not insert more")


def validate_break_plan(
    value: Any,
    owners: dict[str, str],
    shot_durations: dict[str, float],
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append("break_plan must be an object")
        return
    required = value.get("required")
    required_modes = value.get("required_modes")
    occurrences = value.get("occurrences")
    if not isinstance(required, bool):
        errors.append("break_plan.required must be boolean")
    if not isinstance(required_modes, list) or any(mode not in {"person_present", "hands_only_product"} for mode in required_modes):
        errors.append("break_plan.required_modes invalid")
        required_modes = []
    elif len(required_modes) != len(set(required_modes)):
        errors.append("break_plan.required_modes contains duplicates")
    if not isinstance(occurrences, list):
        errors.append("break_plan.occurrences must be an array")
        return
    if required is True and not occurrences:
        errors.append("break_plan.required=true requires observable break occurrences")
    if required is False and required_modes:
        errors.append("break_plan.required=false cannot declare required_modes")
    seen_ids: set[str] = set()
    seen_units: set[str] = set()
    actual_modes: set[str] = set()
    for index, occurrence in enumerate(occurrences):
        path = f"break_plan.occurrences[{index}]"
        if not isinstance(occurrence, dict):
            errors.append(f"{path} must be an object")
            continue
        occurrence_id = occurrence.get("id")
        unit_id = occurrence.get("unit_id")
        shot_id = occurrence.get("shot_id")
        if not nonempty(occurrence_id) or occurrence_id in seen_ids:
            errors.append(f"{path}.id missing or duplicate")
        else:
            seen_ids.add(str(occurrence_id))
        if not isinstance(unit_id, str) or not UNIT_RE.fullmatch(unit_id) or owners.get(unit_id) != shot_id:
            errors.append(f"{path}.unit_id must bind a locked unit owned by shot_id")
        elif unit_id in seen_units:
            errors.append(f"{path}.unit_id duplicates another break event")
        else:
            seen_units.add(unit_id)
        mode = occurrence.get("mode")
        if mode not in {"person_present", "hands_only_product"}:
            errors.append(f"{path}.mode invalid")
        else:
            actual_modes.add(mode)
        origin = occurrence.get("origin")
        if origin == "source":
            if occurrence.get("source_shot_id") != unit_id or not isinstance(unit_id, str) or not SOURCE_RE.fullmatch(unit_id):
                errors.append(f"{path}.source_shot_id must equal the SRC unit_id")
            evidence = occurrence.get("source_evidence")
            if not isinstance(evidence, list) or not evidence or any(not nonempty(item) for item in evidence):
                errors.append(f"{path}.source_evidence missing")
            if "inserted_shot_id" in occurrence:
                errors.append(f"{path} source occurrence cannot carry inserted_shot_id")
        elif origin == "inserted":
            if occurrence.get("inserted_shot_id") != unit_id or not isinstance(unit_id, str) or not INSERTED_RE.fullmatch(unit_id):
                errors.append(f"{path}.inserted_shot_id must equal the ADD unit_id")
            if not nonempty(occurrence.get("insertion_rationale")):
                errors.append(f"{path}.insertion_rationale missing")
            if "source_shot_id" in occurrence:
                errors.append(f"{path} inserted occurrence cannot carry source_shot_id")
        else:
            errors.append(f"{path}.origin must be source or inserted")
        tc_ok = validate_timecode(occurrence.get("generation_timecode"), f"{path}.generation_timecode", errors)
        if tc_ok and isinstance(shot_id, str) and shot_id in shot_durations:
            if occurrence["generation_timecode"]["end"] > shot_durations[shot_id] + 0.001:
                errors.append(f"{path}.generation_timecode exceeds its S duration")
        if not nonempty(occurrence.get("rhythm_rationale")):
            errors.append(f"{path}.rhythm_rationale missing")
        validate_crisp_proof(occurrence.get("crisp_proof"), f"{path}.crisp_proof", errors)
    missing_modes = [mode for mode in required_modes if mode not in actual_modes]
    if missing_modes:
        errors.append(f"break_plan missing required observable modes: {missing_modes}")


def derive_locked_contract(shot_map: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    missing_fields = [field for field in SHOT_MAP_HASH_FIELDS if field not in shot_map]
    if missing_fields:
        errors.append(f"locked shot map missing semantic fields: {missing_fields}")
    expected_semantic_hash = semantic_shot_map_sha256(shot_map)
    if shot_map.get("semantic_payload_version") != "locked-shot-map-six-field-v1":
        errors.append("locked semantic_payload_version must equal locked-shot-map-six-field-v1")
    embedded_semantic_hash = shot_map.get("semantic_sha256")
    if not isinstance(embedded_semantic_hash, str) or not SHA256_RE.fullmatch(embedded_semantic_hash):
        errors.append("locked semantic_sha256 must be a lowercase 64-character SHA-256")
    elif embedded_semantic_hash != expected_semantic_hash:
        errors.append("locked semantic_sha256 does not match the required six-field semantic payload")
    source_duration = shot_map.get("source_duration_seconds")
    if not numeric(source_duration) or source_duration <= 0:
        errors.append("locked source_duration_seconds must be positive")
        source_duration = 0.0
    source_units = shot_map.get("source_units")
    inserted_units = shot_map.get("inserted_units")
    generation_map = shot_map.get("generation_shot_map")
    if not isinstance(source_units, list) or not source_units:
        errors.append("locked source_units must be a non-empty array")
        source_units = []
    if not isinstance(inserted_units, list):
        errors.append("locked inserted_units must be an array")
        inserted_units = []
    if not isinstance(generation_map, list) or not generation_map:
        errors.append("locked generation_shot_map must be a non-empty array")
        generation_map = []

    source_ids: list[str] = []
    inserted_ids: list[str] = []
    owners: dict[str, str] = {}
    units_by_id: dict[str, dict[str, Any]] = {}
    for index, unit in enumerate(source_units):
        unit_id = validate_text_unit(unit, "source", f"source_units[{index}]", errors)
        if unit_id is not None:
            if unit_id in units_by_id:
                errors.append(f"duplicate locked unit {unit_id}")
            source_ids.append(unit_id)
            units_by_id[unit_id] = unit
            owners[unit_id] = str(unit.get("shot_id"))
    for index, unit in enumerate(inserted_units):
        unit_id = validate_text_unit(unit, "inserted", f"inserted_units[{index}]", errors)
        if unit_id is not None:
            if unit_id in units_by_id:
                errors.append(f"duplicate locked unit {unit_id}")
            inserted_ids.append(unit_id)
            units_by_id[unit_id] = unit
            owners[unit_id] = str(unit.get("shot_id"))
    if len(source_ids) != len(set(source_ids)):
        errors.append("locked source_units contain duplicate SRC IDs")
    if len(inserted_ids) != len(set(inserted_ids)):
        errors.append("locked inserted_units contain duplicate ADD IDs")

    source_timeline = [unit for unit in source_units if isinstance(unit, dict) and isinstance(unit.get("source_timecode"), dict)]
    previous_end = 0.0
    for index, unit in enumerate(source_timeline):
        tc = unit["source_timecode"]
        if numeric(tc.get("start")) and abs(float(tc["start"]) - previous_end) > 0.001:
            errors.append(f"source_units[{index}].source_timecode leaves a source timeline gap/overlap")
        if numeric(tc.get("end")):
            previous_end = float(tc["end"])
    if source_timeline and abs(previous_end - float(source_duration)) > 0.001:
        errors.append("source_units do not reach source_duration_seconds")

    shot_ids: list[str] = []
    unit_ids: list[str] = []
    shot_durations: dict[str, float] = {}
    previous_shot_end = 0.0
    for index, entry in enumerate(generation_map):
        path = f"generation_shot_map[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{path} must be an object")
            continue
        shot_id = entry.get("shot_id")
        if not isinstance(shot_id, str) or not SHOT_RE.fullmatch(shot_id):
            errors.append(f"{path}.shot_id invalid")
            continue
        if shot_id in shot_ids:
            errors.append(f"{path}.shot_id duplicated")
        shot_ids.append(shot_id)
        tc_ok = validate_timecode(entry.get("generation_timecode"), f"{path}.generation_timecode", errors)
        if tc_ok:
            shot_tc = entry["generation_timecode"]
            if abs(float(shot_tc["start"]) - previous_shot_end) > 0.001:
                errors.append(f"{path}.generation_timecode leaves a generated timeline gap/overlap")
            previous_shot_end = float(shot_tc["end"])
            shot_durations[shot_id] = float(shot_tc["duration"])
        raw_unit_ids = entry.get("unit_ids")
        validate_id_array(raw_unit_ids, f"{path}.unit_ids", UNIT_RE, errors, allow_empty=False)
        if not isinstance(raw_unit_ids, list):
            raw_unit_ids = []
        raw_source = entry.get("source_shot_ids")
        raw_inserted = entry.get("inserted_shot_ids")
        validate_id_array(raw_source, f"{path}.source_shot_ids", SOURCE_RE, errors)
        validate_id_array(raw_inserted, f"{path}.inserted_shot_ids", INSERTED_RE, errors)
        raw_source = raw_source if isinstance(raw_source, list) else []
        raw_inserted = raw_inserted if isinstance(raw_inserted, list) else []
        if raw_source != [item for item in raw_unit_ids if isinstance(item, str) and SOURCE_RE.fullmatch(item)]:
            errors.append(f"{path}.source_shot_ids must preserve the SRC subsequence of unit_ids")
        if raw_inserted != [item for item in raw_unit_ids if isinstance(item, str) and INSERTED_RE.fullmatch(item)]:
            errors.append(f"{path}.inserted_shot_ids must preserve the ADD subsequence of unit_ids")
        local_previous_end = 0.0
        for unit_id in raw_unit_ids:
            if unit_id not in units_by_id:
                errors.append(f"{path}.unit_ids contains unlocked unit {unit_id}")
                continue
            if owners.get(unit_id) != shot_id:
                errors.append(f"{path}.unit_ids contains {unit_id} owned by {owners.get(unit_id)}")
            local_tc = units_by_id[unit_id].get("generation_timecode")
            if isinstance(local_tc, dict) and numeric(local_tc.get("start")):
                if abs(float(local_tc["start"]) - local_previous_end) > 0.001:
                    errors.append(f"{path} unit {unit_id} leaves a local generation gap/overlap")
                if numeric(local_tc.get("end")):
                    local_previous_end = float(local_tc["end"])
        if tc_ok and raw_unit_ids and abs(local_previous_end - shot_durations[shot_id]) > 0.001:
            errors.append(f"{path} unit timecodes do not cover the full S duration")
        unit_ids.extend(str(item) for item in raw_unit_ids)
    if len(unit_ids) != len(set(unit_ids)):
        errors.append("generation_shot_map repeats a SRC/ADD unit")
    if unit_ids and set(unit_ids) != set(source_ids + inserted_ids):
        missing = [item for item in source_ids + inserted_ids if item not in unit_ids]
        extra = [item for item in unit_ids if item not in units_by_id]
        errors.append(f"generation_shot_map unit coverage mismatch; missing={missing}, extra={extra}")

    validate_eating_plan(
        shot_map.get("eating_plan"),
        float(source_duration),
        owners,
        shot_durations,
        previous_shot_end,
        errors,
    )
    validate_break_plan(shot_map.get("break_plan"), owners, shot_durations, errors)
    return {
        "shot_ids": shot_ids,
        "source_shot_ids": source_ids,
        "inserted_shot_ids": inserted_ids,
        "unit_ids": unit_ids,
        "owners": owners,
        "units_by_id": units_by_id,
        "shot_durations": shot_durations,
        "total_generation_duration": previous_shot_end,
    }


def expected_collections(contract: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "shot_ids": list(contract["shot_ids"]),
        "source_shot_ids": list(contract["source_shot_ids"]),
        "inserted_shot_ids": list(contract["inserted_shot_ids"]),
        "unit_ids": list(contract["unit_ids"]),
    }


def reject_natural_language_alignment(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_ALIGNMENT_KEYS:
                errors.append(f"{path}.{key} forbidden; merge must be deterministic structured JSON, not a natural-language alignment table")
            if key in {"type", "kind", "artifact_type"} and isinstance(item, str) and "alignment_table" in item.lower():
                errors.append(f"{path}.{key} cannot declare an alignment table artifact")
            reject_natural_language_alignment(item, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_natural_language_alignment(item, f"{path}[{index}]", errors)


def validate_common(
    value: dict[str, Any],
    shot_map: dict[str, Any],
    contract: dict[str, Any],
    errors: list[str],
) -> None:
    role = value.get("branch_role")
    if role not in {"image", "text"}:
        errors.append("branch_role must be machine value image or text")
    expected_version = "text-handoff-v2.0" if role == "text" else "image-handoff-v2.1"
    if value.get("schema_version") != expected_version:
        errors.append(f"schema_version must equal {expected_version}; v1 full-delivery handoffs are no longer mergeable")
    if value.get("execution_tier") != "full_delivery":
        errors.append("execution_tier must equal full_delivery")
    semantic_hash = semantic_shot_map_sha256(shot_map)
    for key in ("locked_semantic_hash", "shot_map_sha256"):
        if not isinstance(value.get(key), str) or not SHA256_RE.fullmatch(value[key]):
            errors.append(f"{key} must be a lowercase 64-character SHA-256")
        elif value[key] != semantic_hash:
            errors.append(f"{key} does not match the locked semantic shot-map payload")
    if value.get("locked_semantic_hash") != value.get("shot_map_sha256"):
        errors.append("locked_semantic_hash and shot_map_sha256 must be identical")
    expected = expected_collections(contract)
    if value.get("collections") != expected:
        errors.append("collections must equal the complete locked S/SRC/ADD/unit sets in canonical order")
    allowed_statuses = {"complete", "partial", "blocked"} if role == "text" else {"in_progress", "ready_for_merge", "blocked"}
    if value.get("status") not in allowed_statuses:
        errors.append(f"status must be one of {sorted(allowed_statuses)}")
    for key, pattern in (
        ("completed_shot_ids", SHOT_RE),
        ("completed_source_shot_ids", SOURCE_RE),
        ("completed_inserted_shot_ids", INSERTED_RE),
    ):
        validate_id_array(value.get(key), key, pattern, errors)
        raw = value.get(key)
        expected_key = {
            "completed_shot_ids": "shot_ids",
            "completed_source_shot_ids": "source_shot_ids",
            "completed_inserted_shot_ids": "inserted_shot_ids",
        }[key]
        if isinstance(raw, list) and not ordered_subset(raw, expected[expected_key]):
            errors.append(f"{key} must be an ordered subset of the locked collection")
    if not isinstance(value.get("blocked_items"), list):
        errors.append("blocked_items must be an array")
    if not isinstance(value.get("artifacts"), list):
        errors.append("artifacts must be an array")
    if value.get("status") in {"ready_for_merge", "complete"} and value.get("blocked_items"):
        errors.append("completed/ready handoff cannot contain blocked_items")
    if value.get("status") == "blocked" and not value.get("blocked_items"):
        errors.append("blocked handoff requires observable blocked_items")
    reject_natural_language_alignment(value, "$", errors)


def validate_text(
    value: dict[str, Any],
    shot_map: dict[str, Any],
    contract: dict[str, Any],
    errors: list[str],
) -> None:
    for field in SHOT_MAP_HASH_FIELDS:
        if value.get(field) != shot_map.get(field):
            errors.append(f"text handoff {field} differs from the locked semantic payload")
    source_units = value.get("source_units") if isinstance(value.get("source_units"), list) else []
    inserted_units = value.get("inserted_units") if isinstance(value.get("inserted_units"), list) else []
    actual_source = [unit.get("source_shot_id") for unit in source_units if isinstance(unit, dict)]
    actual_inserted = [unit.get("inserted_shot_id") for unit in inserted_units if isinstance(unit, dict)]
    if actual_source != contract["source_shot_ids"]:
        errors.append("text source_units must contain every locked SRC exactly once in canonical order")
    if actual_inserted != contract["inserted_shot_ids"]:
        errors.append("text inserted_units must contain every locked ADD exactly once in canonical order")
    if value.get("status") == "complete":
        if value.get("completed_shot_ids") != contract["shot_ids"]:
            errors.append("complete text handoff must cover the locked S order exactly")
        if value.get("completed_source_shot_ids") != contract["source_shot_ids"]:
            errors.append("complete text handoff must cover the locked SRC order exactly")
        if value.get("completed_inserted_shot_ids") != contract["inserted_shot_ids"]:
            errors.append("complete text handoff must cover the locked ADD order exactly")


def validate_package_integration_qa(unit: dict[str, Any], path: str, errors: list[str]) -> None:
    """Require measurable box geometry and scene integration, not an Agent assertion."""
    packaging_visible = bool((unit.get("packaging_evidence") or {}).get("visible"))
    if not packaging_visible:
        return
    qa = unit.get("qa") if isinstance(unit.get("qa"), dict) else {}
    integration = qa.get("package_integration") if isinstance(qa.get("package_integration"), dict) else None
    if not isinstance(integration, dict):
        errors.append(f"{path}.qa.package_integration missing while packaging is visible")
        return
    measurements = integration.get("box_measurements")
    if not isinstance(measurements, list) or not measurements:
        errors.append(f"{path}.qa.package_integration.box_measurements must measure every visible box")
    else:
        box_ids: list[str] = []
        for index, item in enumerate(measurements):
            item_path = f"{path}.qa.package_integration.box_measurements[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_path} must be an object")
                continue
            box_id = item.get("box_id")
            if not nonempty(box_id):
                errors.append(f"{item_path}.box_id missing")
            else:
                box_ids.append(str(box_id))
            front_ratio = item.get("front_width_height_ratio")
            thickness_ratio = item.get("thickness_front_ratio")
            if not isinstance(front_ratio, (int, float)) or isinstance(front_ratio, bool) or not 0.95 <= front_ratio <= 1.05:
                errors.append(f"{item_path}.front_width_height_ratio must be 0.95–1.05 for the square 15×15 cm face")
            if not isinstance(thickness_ratio, (int, float)) or isinstance(thickness_ratio, bool) or not 0.25 <= thickness_ratio <= 0.35:
                errors.append(f"{item_path}.thickness_front_ratio must be 0.25–0.35 for the 4.5 cm thickness")
            if item.get("same_size_as_peer_boxes") is not True:
                errors.append(f"{item_path}.same_size_as_peer_boxes must be true")
        if len(box_ids) != len(set(box_ids)):
            errors.append(f"{path}.qa.package_integration.box_measurements repeats a box_id")
    for field in ("scene_light_match", "contact_shadow", "edge_blend"):
        if integration.get(field) != "matched":
            errors.append(f"{path}.qa.package_integration.{field} must equal matched")
    if integration.get("flat_cutout") is not False:
        errors.append(f"{path}.qa.package_integration.flat_cutout must be false")
    if not nonempty(integration.get("observable_evidence")):
        errors.append(f"{path}.qa.package_integration.observable_evidence missing")


def validate_ready_gallery_approval(
    receipt: Any,
    approved: list[tuple[str, list[tuple[str, str, str]]]],
    errors: list[str],
) -> None:
    """Bind ready state to the exact user-visible gallery and its asset hashes."""
    if not isinstance(receipt, dict):
        errors.append("ready image handoff requires gallery_receipt")
        return
    if receipt.get("status") != "user_approved":
        errors.append("gallery_receipt.status must equal user_approved")
    for field in ("display_receipt_id", "displayed_at", "approved_at"):
        if not nonempty(receipt.get(field)):
            errors.append(f"gallery_receipt.{field} missing")
    expected = [
        {"unit_id": unit_id, "asset_id": asset_id, "sha256": image_hash}
        for unit_id, records in approved
        for asset_id, _, image_hash in records
    ]
    if receipt.get("asset_refs") != expected:
        errors.append("gallery_receipt.asset_refs must preserve the exact approved asset order/hash shown to and approved by the user")


def validate_image_unit(
    unit: Any,
    index: int,
    contract: dict[str, Any],
    errors: list[str],
) -> tuple[str, list[tuple[str, str, str]]] | None:
    path = f"units[{index}]"
    if not isinstance(unit, dict):
        errors.append(f"{path} must be an object")
        return None
    if "approved_asset" in unit or "delivery_asset_ids" in unit:
        errors.append(f"{path} must carry approved_assets[]; legacy approved_asset/delivery_asset_ids is forbidden")
    unit_id = unit.get("unit_id")
    unit_type = unit.get("unit_type")
    shot_id = unit.get("shot_id")
    if not isinstance(unit_id, str) or not UNIT_RE.fullmatch(unit_id) or unit_id not in contract["units_by_id"]:
        errors.append(f"{path}.unit_id invalid or unlocked")
        return None
    expected_type = "source" if SOURCE_RE.fullmatch(unit_id) else "inserted"
    expected_key = "source_shot_id" if expected_type == "source" else "inserted_shot_id"
    forbidden_key = "inserted_shot_id" if expected_type == "source" else "source_shot_id"
    if unit_type != expected_type:
        errors.append(f"{path}.unit_type must equal {expected_type}")
    if unit.get(expected_key) != unit_id or forbidden_key in unit:
        errors.append(f"{path} must bind exactly its own {expected_key}")
    if shot_id != contract["owners"].get(unit_id):
        errors.append(f"{path}.shot_id differs from the locked owner")
    locked_unit = contract["units_by_id"][unit_id]
    fields = SOURCE_CARD_FIELDS if expected_type == "source" else INSERTED_CARD_FIELDS
    for field in fields:
        if unit.get(field) != locked_unit.get(field):
            errors.append(f"{path}.{field} differs from the locked/text card")
    # Revalidate the mirrored card so an empty six-layer object cannot pass by hash alone.
    validate_text_unit(unit, expected_type, path, errors)
    approved_assets = unit.get("approved_assets")
    asset_tuples: list[tuple[str, str, str]] = []
    if not isinstance(approved_assets, list) or not approved_assets:
        errors.append(f"{path}.approved_assets must contain at least one approved target frame")
        approved_assets = []
    for asset_index, asset in enumerate(approved_assets):
        asset_path = f"{path}.approved_assets[{asset_index}]"
        asset_tuple = check_absolute_file_asset(asset, asset_path, errors, image=True)
        asset = asset if isinstance(asset, dict) else {}
        if asset.get("approval_status") != "user_approved":
            errors.append(f"{asset_path}.approval_status must equal user_approved; visual self-review alone cannot enter Word")
        user_approval = asset.get("user_approval") if isinstance(asset.get("user_approval"), dict) else {}
        if user_approval.get("status") != "user_approved":
            errors.append(f"{asset_path}.user_approval.status must equal user_approved")
        if user_approval.get("asset_sha256") != asset.get("sha256"):
            errors.append(f"{asset_path}.user_approval.asset_sha256 must bind the approved bytes")
        for field in ("display_receipt_id", "approved_at"):
            if not nonempty(user_approval.get(field)):
                errors.append(f"{asset_path}.user_approval.{field} missing")
        if not nonempty(asset.get("responsibility")):
            errors.append(f"{asset_path}.responsibility missing")
        width, height = asset.get("width"), asset.get("height")
        if not all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in (width, height)):
            errors.append(f"{asset_path} width/height invalid")
        elif width * 16 != height * 9:
            errors.append(f"{asset_path} must be a 9:16 image")
        if asset_tuple is not None:
            asset_tuples.append(asset_tuple)
    for position, label in ((0, "asset_id"), (1, "image_path"), (2, "sha256")):
        values = [item[position] for item in asset_tuples]
        if len(values) != len(set(values)):
            errors.append(f"{path}.approved_assets {label} must be unique within one unit")
    responsibilities = [
        str(asset.get("responsibility")).strip()
        for asset in approved_assets
        if isinstance(asset, dict) and nonempty(asset.get("responsibility"))
    ]
    if len(approved_assets) > 1 and len(responsibilities) == len(approved_assets) and len(responsibilities) != len(set(responsibilities)):
        errors.append(f"{path}.approved_assets responsibilities must describe distinct action states")
    qa = unit.get("qa")
    if not isinstance(qa, dict):
        errors.append(f"{path}.qa missing")
    else:
        if qa.get("status") != "approved":
            errors.append(f"{path}.qa.status must equal approved")
        if not nonempty(qa.get("observable_evidence")):
            errors.append(f"{path}.qa.observable_evidence missing")
        if qa.get("six_layers_verified") is not True:
            errors.append(f"{path}.qa.six_layers_verified must be true")
        packaging_visible = bool((unit.get("packaging_evidence") or {}).get("visible"))
        if packaging_visible and qa.get("packaging_visible_faces_verified") is not True:
            errors.append(f"{path}.qa.packaging_visible_faces_verified must be true when packaging is visible")
        elif not isinstance(qa.get("packaging_visible_faces_verified"), bool):
            errors.append(f"{path}.qa.packaging_visible_faces_verified must be boolean")
        validate_package_integration_qa(unit, path, errors)
    if not asset_tuples:
        return None
    return unit_id, asset_tuples


def validate_plan_review(
    review: Any,
    plan: Any,
    units_by_id: dict[str, dict[str, Any]],
    path: str,
    errors: list[str],
    *,
    include_mode: bool,
) -> None:
    if not isinstance(review, dict) or not isinstance(review.get("occurrences"), list):
        errors.append(f"{path}.occurrences must be an array")
        return
    planned = (plan or {}).get("occurrences") if isinstance(plan, dict) else []
    planned = planned if isinstance(planned, list) else []
    reviews = review["occurrences"]
    expected_ids = [item.get("id") for item in planned if isinstance(item, dict)]
    actual_ids = [item.get("id") for item in reviews if isinstance(item, dict)]
    if actual_ids != expected_ids:
        errors.append(f"{path}.occurrences must review every locked occurrence exactly once in order")
    planned_by_id = {str(item.get("id")): item for item in planned if isinstance(item, dict)}
    seen: set[str] = set()
    for index, occurrence in enumerate(reviews):
        occurrence_path = f"{path}.occurrences[{index}]"
        if not isinstance(occurrence, dict):
            errors.append(f"{occurrence_path} must be an object")
            continue
        occurrence_id = occurrence.get("id")
        if not nonempty(occurrence_id) or occurrence_id in seen:
            errors.append(f"{occurrence_path}.id missing or duplicate")
        else:
            seen.add(str(occurrence_id))
        planned_occurrence = planned_by_id.get(str(occurrence_id))
        unit_id = occurrence.get("unit_id")
        shot_id = occurrence.get("shot_id")
        if planned_occurrence is None:
            errors.append(f"{occurrence_path}.id is not in the locked plan")
        else:
            if unit_id != planned_occurrence.get("unit_id") or shot_id != planned_occurrence.get("shot_id"):
                errors.append(f"{occurrence_path} unit/shot binding differs from locked plan")
            if include_mode and occurrence.get("mode") != planned_occurrence.get("mode"):
                errors.append(f"{occurrence_path}.mode differs from locked break plan")
        if occurrence.get("status") != "approved":
            errors.append(f"{occurrence_path}.status must equal approved")
        if not nonempty(occurrence.get("observable_evidence")):
            errors.append(f"{occurrence_path}.observable_evidence missing")
        unit = units_by_id.get(str(unit_id))
        owner_asset_ids = [
            str(asset.get("asset_id"))
            for asset in ((unit or {}).get("approved_assets") or [])
            if isinstance(asset, dict) and nonempty(asset.get("asset_id"))
        ]
        evidence_asset_ids = occurrence.get("evidence_asset_ids")
        if (
            not isinstance(evidence_asset_ids, list)
            or not evidence_asset_ids
            or len(evidence_asset_ids) != len(set(evidence_asset_ids))
            or any(asset_id not in owner_asset_ids for asset_id in evidence_asset_ids)
        ):
            errors.append(f"{occurrence_path}.evidence_asset_ids must be a non-empty subset of that unit's approved assets")


def validate_candidate_progress(value: Any, contract: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("candidate_progress must be an array")
        return
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        path = f"candidate_progress[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        if item.get("unit_id") not in contract["unit_ids"]:
            errors.append(f"{path}.unit_id invalid or unlocked")
        if item.get("display_label") != "候选/未批准":
            errors.append(f"{path}.display_label must equal 候选/未批准")
        asset = item.get("candidate_asset")
        asset_tuple = check_absolute_file_asset(asset, f"{path}.candidate_asset", errors, image=True)
        if isinstance(asset, dict) and asset.get("approval_status") != "candidate_unapproved":
            errors.append(f"{path}.candidate_asset.approval_status must equal candidate_unapproved")
        if asset_tuple is not None:
            if asset_tuple[0] in seen_ids:
                errors.append(f"{path}.candidate_asset.asset_id duplicated")
            seen_ids.add(asset_tuple[0])


def validate_image(
    value: dict[str, Any],
    shot_map: dict[str, Any],
    contract: dict[str, Any],
    errors: list[str],
) -> None:
    for field in ("source_duration_seconds", "generation_shot_map", "eating_plan", "break_plan"):
        if value.get(field) != shot_map.get(field):
            errors.append(f"image handoff {field} differs from the locked semantic payload")
    units = value.get("units")
    if not isinstance(units, list):
        errors.append("image handoff units must be an array")
        units = []
    approved: list[tuple[str, list[tuple[str, str, str]]]] = []
    units_by_id: dict[str, dict[str, Any]] = {}
    for index, unit in enumerate(units):
        item = validate_image_unit(unit, index, contract, errors)
        if item is not None:
            approved.append(item)
            units_by_id[item[0]] = unit
    actual_unit_ids = [item[0] for item in approved]
    if len(actual_unit_ids) != len(set(actual_unit_ids)):
        errors.append("image handoff repeats a SRC/ADD unit")
    if not ordered_subset(actual_unit_ids, contract["unit_ids"]):
        errors.append("image handoff units must follow the locked mixed SRC/ADD order")
    flattened_assets: list[tuple[str, str, str, str]] = [
        (unit_id, asset_id, image_path, image_hash)
        for unit_id, records in approved
        for asset_id, image_path, image_hash in records
    ]
    for position, label in ((1, "asset_id"), (2, "image_path"), (3, "sha256")):
        owners: dict[str, str] = {}
        for record in flattened_assets:
            unit_id, asset_value = record[0], record[position]
            prior_owner = owners.get(asset_value)
            if prior_owner and prior_owner != unit_id:
                errors.append(f"approved_assets.{label} is reused across {prior_owner} and {unit_id}; one unit cannot fill another unit's target-frame coverage")
            owners[asset_value] = unit_id
    actual_source = [unit_id for unit_id in actual_unit_ids if SOURCE_RE.fullmatch(unit_id)]
    actual_inserted = [unit_id for unit_id in actual_unit_ids if INSERTED_RE.fullmatch(unit_id)]
    complete_shots = [
        shot_id
        for shot_id in contract["shot_ids"]
        if all(unit_id in actual_unit_ids for unit_id in [item for item in contract["unit_ids"] if contract["owners"].get(item) == shot_id])
    ]
    if value.get("completed_source_shot_ids") != actual_source:
        errors.append("completed_source_shot_ids must equal approved SRC units in canonical order")
    if value.get("completed_inserted_shot_ids") != actual_inserted:
        errors.append("completed_inserted_shot_ids must equal approved ADD units in canonical order")
    if value.get("completed_shot_ids") != complete_shots:
        errors.append("completed_shot_ids may include only S cards whose every locked unit has an approved image")
    if value.get("status") == "ready_for_merge":
        if actual_unit_ids != contract["unit_ids"]:
            missing = [item for item in contract["unit_ids"] if item not in actual_unit_ids]
            errors.append(f"ready image handoff must cover every locked SRC/ADD in order; missing={missing}")
        if value.get("completed_shot_ids") != contract["shot_ids"]:
            errors.append("ready image handoff must cover the complete locked S order")
        validate_ready_gallery_approval(value.get("gallery_receipt"), approved, errors)
    controller = value.get("controller_reply")
    required_controller = {
        "must_inline_images": True,
        "may_only_report_path": False,
        "deliver_when_ready": True,
        "final_ready_requires_per_unit_gallery": True,
        "candidate_display_label": "候选/未批准",
    }
    if not isinstance(controller, dict):
        errors.append("controller_reply missing")
    else:
        for key, expected in required_controller.items():
            if controller.get(key) != expected:
                errors.append(f"controller_reply.{key} must equal {expected!r}")
        if controller.get("gallery_unit_ids") != actual_unit_ids:
            errors.append("controller_reply.gallery_unit_ids must equal approved unit order for direct inline rendering")
        if value.get("status") == "ready_for_merge" and controller.get("gallery_unit_ids") != contract["unit_ids"]:
            errors.append("final ready controller gallery must inline every SRC/ADD approved image")
        expected_gallery_refs = [
            {"unit_id": unit_id, "asset_id": asset_id}
            for unit_id, records in approved
            for asset_id, _, _ in records
        ]
        if controller.get("gallery_asset_refs") != expected_gallery_refs:
            errors.append("controller_reply.gallery_asset_refs must list every approved image in unit/asset order")
    validate_candidate_progress(value.get("candidate_progress"), contract, errors)
    if value.get("status") == "ready_for_merge":
        validate_plan_review(value.get("eating_plan_review"), value.get("eating_plan"), units_by_id, "eating_plan_review", errors, include_mode=False)
        validate_plan_review(value.get("break_plan_review"), value.get("break_plan"), units_by_id, "break_plan_review", errors, include_mode=True)


def validate_handoff(
    handoff: dict[str, Any],
    shot_map: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    contract = derive_locked_contract(shot_map, errors)
    validate_common(handoff, shot_map, contract, errors)
    if handoff.get("branch_role") == "text":
        validate_text(handoff, shot_map, contract, errors)
    elif handoff.get("branch_role") == "image":
        validate_image(handoff, shot_map, contract, errors)
    return errors, contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a v2 full-delivery branch handoff against one locked semantic shot map.")
    parser.add_argument("--handoff", required=True, type=Path)
    parser.add_argument("--locked-shot-map", required=True, type=Path)
    args = parser.parse_args()
    handoff_path = args.handoff.expanduser().resolve()
    shot_map_path = args.locked_shot_map.expanduser().resolve()
    handoff = load_object(handoff_path)
    shot_map = load_object(shot_map_path)
    errors, contract = validate_handoff(handoff, shot_map)
    result = {
        "handoff": str(handoff_path),
        "branch_role": handoff.get("branch_role"),
        "schema_version": handoff.get("schema_version"),
        "status": "valid" if not errors else "blocked",
        "locked_counts": {
            "S": len(contract.get("shot_ids", [])),
            "SRC": len(contract.get("source_shot_ids", [])),
            "ADD": len(contract.get("inserted_shot_ids", [])),
        },
        "error_count": len(errors),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
