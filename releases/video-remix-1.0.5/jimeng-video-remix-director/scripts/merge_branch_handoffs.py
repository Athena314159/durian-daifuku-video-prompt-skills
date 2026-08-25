#!/usr/bin/env python3
"""Deterministically merge validated v2 image/text handoffs into one canonical JSON package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from validate_branch_handoff import canonical_json, load_object, semantic_shot_map_sha256, validate_handoff


def build_package(
    image: dict[str, Any],
    text: dict[str, Any],
    locked: dict[str, Any],
) -> dict[str, Any]:
    image_by_id = {unit["unit_id"]: unit for unit in image["units"]}
    text_by_id: dict[str, dict[str, Any]] = {}
    for unit in text["source_units"]:
        text_by_id[unit["source_shot_id"]] = unit
    for unit in text["inserted_units"]:
        text_by_id[unit["inserted_shot_id"]] = unit
    cards: list[dict[str, Any]] = []
    source_units: list[dict[str, Any]] = []
    inserted_units: list[dict[str, Any]] = []
    gallery_entries: list[dict[str, Any]] = []
    for unit_id in text["collections"]["unit_ids"]:
        text_unit = text_by_id[unit_id]
        image_unit = image_by_id[unit_id]
        unit_type = "source" if unit_id.startswith("SRC") else "inserted"
        merged = {
            "unit_id": unit_id,
            "unit_type": unit_type,
            **text_unit,
            "approved_assets": image_unit["approved_assets"],
            "image_qa": image_unit["qa"],
        }
        cards.append(merged)
        (source_units if unit_type == "source" else inserted_units).append(merged)
        for asset in image_unit["approved_assets"]:
            gallery_entries.append(
                {
                    "unit_id": unit_id,
                    "shot_id": text_unit["shot_id"],
                    "label": f"{unit_id}｜{asset['asset_id']}｜{asset['responsibility']}",
                    "asset_id": asset["asset_id"],
                    "image_path": asset["image_path"],
                    "sha256": asset["sha256"],
                    "responsibility": asset["responsibility"],
                    "approval_status": "user_approved",
                    "user_approval": asset["user_approval"],
                }
            )
    payload: dict[str, Any] = {
        "schema_version": "full-delivery-merged-v2.0",
        "package_kind": "canonical_structured_merge",
        "locked_semantic_hash": semantic_shot_map_sha256(locked),
        "collections": text["collections"],
        "source_duration_seconds": text["source_duration_seconds"],
        "generation_shot_map": text["generation_shot_map"],
        "cards": cards,
        "source_units": source_units,
        "inserted_units": inserted_units,
        "eating_plan": text["eating_plan"],
        "break_plan": text["break_plan"],
        "eating_plan_review": image["eating_plan_review"],
        "break_plan_review": image["break_plan_review"],
        "controller_gallery": {
            "must_inline_images": True,
            "may_only_report_path": False,
            "deliver_when_ready": True,
            "final_ready_requires_per_unit_gallery": True,
            "entries": gallery_entries,
            "gallery_receipt": image["gallery_receipt"],
        },
        "merge_invariants": {
            "machine_exact": True,
            "natural_language_table_allowed": False,
            "at_least_one_approved_image_per_src_add": True,
            "multiple_action_state_images_per_unit_allowed": True,
            "cross_unit_asset_path_hash_reuse_forbidden": True,
            "src_add_s_order_compared": True,
            "semantic_hash_compared": True,
        },
    }
    payload["canonical_payload_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge two validated full-delivery v2 handoffs without a natural-language alignment table.")
    parser.add_argument("--image-handoff", required=True, type=Path)
    parser.add_argument("--text-handoff", required=True, type=Path)
    parser.add_argument("--locked-shot-map", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    image = load_object(args.image_handoff.expanduser().resolve())
    text = load_object(args.text_handoff.expanduser().resolve())
    locked = load_object(args.locked_shot_map.expanduser().resolve())
    errors: list[str] = []
    if image.get("branch_role") != "image":
        errors.append("--image-handoff must have branch_role=image")
    if text.get("branch_role") != "text":
        errors.append("--text-handoff must have branch_role=text")
    image_errors, _ = validate_handoff(image, locked)
    text_errors, _ = validate_handoff(text, locked)
    errors.extend(f"image: {item}" for item in image_errors)
    errors.extend(f"text: {item}" for item in text_errors)
    if image.get("status") != "ready_for_merge":
        errors.append("image status must be ready_for_merge")
    if text.get("status") != "complete":
        errors.append("text status must be complete")
    for key in ("locked_semantic_hash", "shot_map_sha256", "collections"):
        if image.get(key) != text.get(key):
            errors.append(f"branches differ on {key}")
    for key in ("generation_shot_map", "eating_plan", "break_plan"):
        if image.get(key) != text.get(key):
            errors.append(f"branches differ on {key}")
    if errors:
        print(json.dumps({"status": "blocked", "error_count": len(errors), "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    package = build_package(image, text, locked)
    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "merged",
                "schema_version": package["schema_version"],
                "counts": {key: len(value) for key, value in package["collections"].items()},
                "canonical_payload_sha256": package["canonical_payload_sha256"],
                "output": str(out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
