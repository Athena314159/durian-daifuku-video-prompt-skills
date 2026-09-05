#!/usr/bin/env python3
"""Validate source-intake handoffs and keep normal next-stage inputs non-blocking."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"[0-9a-f]{64}")
SOURCE_ID_RE = re.compile(r"SRC([0-9]+)")
ALLOWED_PENDING = {"revised_script", "target_product_reference"}
LANGUAGE_EVIDENCE = {"visible_subtitles", "automatic_language_detection", "speech_audio", "lip_reading"}
LANGUAGE_PRIORITY = ["visible_subtitles", "automatic_language_detection", "speech_audio", "lip_reading"]
EXCLUDED_LANGUAGE_SIGNALS = {"product_name", "brand_name", "country_name", "origin_label"}
TIMECODE_TOLERANCE_SECONDS = 0.001


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("handoff must be a JSON object")
    return value


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def source_id_number(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = SOURCE_ID_RE.fullmatch(value)
    return int(match.group(1)) if match else None


def validate(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != "source-intake-handoff-v1.0":
        errors.append("schema_version must equal source-intake-handoff-v1.0")
    if value.get("execution_tier") != "source_intake":
        errors.append("execution_tier must equal source_intake")
    role = value.get("branch_role")
    if role not in {"image", "text"}:
        errors.append("branch_role must be image or text")
    digest = value.get("source_intake_contract_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        errors.append("source_intake_contract_sha256 must be a lowercase SHA-256")

    product_mode = value.get("product_mode")
    if product_mode not in {"preserve_source_product", "replace_product"}:
        errors.append("product_mode must be preserve_source_product or replace_product")
    reference_bound = value.get("target_product_reference_bound")
    if not isinstance(reference_bound, bool):
        errors.append("target_product_reference_bound must be boolean")

    status = value.get("status")
    if status not in {"source_inventory_ready", "transcript_ready", "awaiting_inputs", "blocked"}:
        errors.append("status is not valid for source intake")
    pending = value.get("pending_inputs")
    if not isinstance(pending, list) or any(item not in ALLOWED_PENDING for item in pending) or len(pending) != len(set(pending)):
        errors.append("pending_inputs must be a unique array of known next-stage inputs")
        pending = []
    blockers = value.get("blocked_items")
    if not isinstance(blockers, list):
        errors.append("blocked_items must be an array")
        blockers = []
    if status == "blocked" and not blockers:
        errors.append("blocked status requires observable blocked_items")
    if status == "blocked":
        for index, item in enumerate(blockers):
            if not isinstance(item, dict):
                errors.append(f"blocked_items[{index}] must be an object")
                continue
            if item.get("kind") not in {"technical_error", "fact_conflict", "validation_failure"}:
                errors.append(f"blocked_items[{index}].kind is not a true blocking class")
            if not isinstance(item.get("code"), str) or not item["code"].strip():
                errors.append(f"blocked_items[{index}].code missing")
            if not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
                errors.append(f"blocked_items[{index}].evidence missing")
    if status != "blocked" and blockers:
        errors.append("non-blocked status cannot contain blocked_items")
    if status == "awaiting_inputs" and not pending:
        errors.append("awaiting_inputs status requires pending_inputs")

    if product_mode == "preserve_source_product":
        if reference_bound is True:
            errors.append("preserve_source_product must not bind a target product reference")
        if "target_product_reference" in pending:
            errors.append("preserve_source_product must not await a target product reference")
        if value.get("target_product_references"):
            errors.append("preserve_source_product must not require target_product_references")
    elif reference_bound is False and status != "blocked" and "target_product_reference" not in pending:
        errors.append("replace_product without a bound reference must declare target_product_reference as pending")

    if not isinstance(value.get("artifacts"), list):
        errors.append("artifacts must be an array")

    if role == "image":
        inventory = value.get("source_inventory")
        if not isinstance(inventory, dict) or not isinstance(inventory.get("source_shots"), list) or not inventory["source_shots"]:
            errors.append("image source intake requires a non-empty source_inventory.source_shots")
        elif status == "source_inventory_ready":
            expected_ids = inventory.get("source_shot_ids")
            if (
                not isinstance(expected_ids, list)
                or not expected_ids
                or any(source_id_number(item) is None for item in expected_ids)
                or len(expected_ids) != len(set(expected_ids))
            ):
                errors.append("ready image source intake requires unique source_inventory.source_shot_ids")
                expected_ids = []
            elif [source_id_number(item) for item in expected_ids] != list(range(1, len(expected_ids) + 1)):
                errors.append("source_inventory.source_shot_ids must be a complete contiguous SRC sequence starting at SRC1")

            source_shots = inventory["source_shots"]
            actual_ids: list[str] = []
            for index, shot in enumerate(source_shots):
                shot_path = f"source_inventory.source_shots[{index}]"
                if not isinstance(shot, dict):
                    errors.append(f"{shot_path} must be an object")
                    continue

                source_id = shot.get("source_shot_id")
                if source_id_number(source_id) is None:
                    errors.append(f"{shot_path}.source_shot_id must be an SRC identifier")
                else:
                    actual_ids.append(source_id)

                timecode = shot.get("timecode")
                if not isinstance(timecode, dict):
                    errors.append(f"{shot_path}.timecode missing")
                else:
                    start, end, duration = timecode.get("start"), timecode.get("end"), timecode.get("duration")
                    if not all(is_number(item) for item in (start, end, duration)):
                        errors.append(f"{shot_path}.timecode must contain numeric start, end and duration")
                    elif start < 0 or end <= start or duration <= 0:
                        errors.append(f"{shot_path}.timecode has invalid bounds")
                    elif abs(float(duration) - (float(end) - float(start))) > TIMECODE_TOLERANCE_SECONDS:
                        errors.append(f"{shot_path}.timecode.duration must equal end-start")

                image_path = shot.get("image_path")
                if not isinstance(image_path, str) or not image_path.strip():
                    errors.append(f"{shot_path}.image_path missing")
                else:
                    frame = Path(image_path)
                    if not frame.is_absolute():
                        errors.append(f"{shot_path}.image_path must be an absolute path")
                    elif not frame.exists():
                        errors.append(f"{shot_path}.image_path does not exist")
                    elif not frame.is_file():
                        errors.append(f"{shot_path}.image_path must point to a file")

                caption = shot.get("caption")
                if not isinstance(caption, str) or not caption.strip():
                    errors.append(f"{shot_path}.caption missing")

            if len(actual_ids) != len(set(actual_ids)):
                errors.append("source_inventory.source_shots contains duplicate SRC identifiers")
            if expected_ids and set(actual_ids) != set(expected_ids):
                missing = sorted(set(expected_ids) - set(actual_ids), key=lambda item: source_id_number(item) or -1)
                extra = sorted(set(actual_ids) - set(expected_ids), key=lambda item: source_id_number(item) or -1)
                errors.append(
                    "source_inventory.source_shots must cover every declared SRC exactly once"
                    f"; missing={missing}; extra={extra}"
                )

            reply = value.get("controller_reply")
            if not isinstance(reply, dict):
                errors.append("ready image source intake requires controller_reply")
            else:
                if reply.get("must_inline_images") is not True:
                    errors.append("controller must inline every source image")
                if reply.get("may_only_report_path") is not False:
                    errors.append("controller may not report only an image handoff path")
                if reply.get("deliver_when_ready") is not True:
                    errors.append("controller must deliver the source gallery as soon as it is ready")
        if status == "transcript_ready":
            errors.append("image source intake cannot use transcript_ready")

    if role == "text":
        detection = value.get("language_detection")
        if not isinstance(detection, dict):
            errors.append("text source intake requires language_detection")
        else:
            if detection.get("decision_source") not in {"visible_subtitles", "automatic_language_detection", "speech_audio"}:
                errors.append("language decision must come from subtitles, automatic detection or speech audio")
            if detection.get("evidence_priority") != LANGUAGE_PRIORITY:
                errors.append("language evidence_priority must prefer visible subtitles and automatic detection")
            evidence_used = detection.get("evidence_used")
            if not isinstance(evidence_used, list) or not evidence_used or any(item not in LANGUAGE_EVIDENCE for item in evidence_used):
                errors.append("language evidence_used contains an unsupported signal")
            excluded = detection.get("excluded_signals")
            if not isinstance(excluded, list) or not EXCLUDED_LANGUAGE_SIGNALS.issubset(set(excluded)):
                errors.append("language_detection.excluded_signals must exclude product, brand and country/origin names")

        transcript = value.get("transcript")
        if not isinstance(transcript, dict):
            errors.append("text source intake requires transcript")
        else:
            if not isinstance(transcript.get("source_language"), str) or len(transcript["source_language"].strip()) < 2:
                errors.append("transcript.source_language missing")
            if not isinstance(transcript.get("editable_text"), str) or not transcript["editable_text"].strip():
                errors.append("transcript.editable_text missing")
            segments = transcript.get("segments")
            if not isinstance(segments, list) or not segments:
                errors.append("transcript.segments must be non-empty")
            else:
                for index, segment in enumerate(segments):
                    path = f"transcript.segments[{index}]"
                    if not isinstance(segment, dict):
                        errors.append(f"{path} must be an object")
                        continue
                    start, end = segment.get("start"), segment.get("end")
                    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (start, end)) or end <= start or start < 0:
                        errors.append(f"{path} has invalid time bounds")
                    if not isinstance(segment.get("text"), str) or not segment["text"].strip():
                        errors.append(f"{path}.text missing")
                    if not isinstance(segment.get("evidence"), list) or not segment["evidence"]:
                        errors.append(f"{path}.evidence missing")
                    confidence = segment.get("confidence")
                    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                        errors.append(f"{path}.confidence invalid")

        reply = value.get("controller_reply")
        if not isinstance(reply, dict):
            errors.append("text source intake requires controller_reply")
        else:
            if reply.get("must_inline_editable_text") is not True:
                errors.append("controller must inline transcript.editable_text")
            if reply.get("may_only_report_path") is not False:
                errors.append("controller may not report only a handoff path")
            if reply.get("deliver_before_other_branch_complete") is not True:
                errors.append("controller must deliver transcript before the other branch completes")
        if status == "source_inventory_ready":
            errors.append("text source intake cannot use source_inventory_ready")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a source-intake branch handoff.")
    parser.add_argument("--handoff", required=True, type=Path)
    args = parser.parse_args()
    try:
        value = load_object(args.handoff.expanduser().resolve())
        errors = validate(value)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"status": "valid" if not errors else "invalid", "error_count": len(errors), "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
