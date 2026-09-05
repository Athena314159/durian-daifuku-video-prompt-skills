#!/usr/bin/env python3
"""Positive and adversarial regressions for full-delivery handoff v2.0."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from validate_branch_handoff import semantic_shot_map_sha256


SCRIPT = Path(__file__).resolve().parent / "validate_branch_handoff.py"
CASES = 0


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(handoff: Path, shot_map: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--handoff", str(handoff), "--locked-shot-map", str(shot_map)],
        capture_output=True,
        text=True,
        check=False,
    )


def expect(valid: bool, handoff: Path, shot_map: Path, needle: str = "") -> None:
    global CASES
    CASES += 1
    result = run(handoff, shot_map)
    expected = 0 if valid else 2
    assert result.returncode == expected, result.stdout + result.stderr
    if needle:
        assert needle in result.stdout, result.stdout


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tc(start: float, end: float) -> dict[str, float]:
    return {"start": start, "end": end, "duration": end - start}


def layers(*, inserted: bool = False, frame: str = "source-evidence.jpg") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "emotion_trigger",
        "gaze",
        "facial_microreaction",
        "body_hand_preparation",
        "breath_pause",
        "voice_speech",
    ):
        if inserted:
            result[key] = {
                "status": "template_supplement",
                "source_timecode": None,
                "source_reference_frame": None,
                "observable_evidence": f"{key} 按已绑定原片节奏补足，不冒充源片观察。",
                "confidence": 0.8,
                "gap_reason": "新增镜头无独立原片首帧，使用命名参考补足。",
            }
        else:
            result[key] = {
                "status": "observed",
                "source_timecode": tc(0.0, 1.0),
                "source_reference_frame": frame,
                "observable_evidence": f"原片可观察的 {key} 证据。",
                "confidence": 0.95,
                "gap_reason": None,
            }
    return result


def no_package() -> dict[str, Any]:
    return {"visible": False, "visible_faces": []}


def crisp() -> dict[str, Any]:
    return {
        "single_snap": True,
        "fracture_visible": True,
        "material_conservation_locked": True,
        "crumbs": {"minimum": 3, "maximum": 6},
        "complementary_orange_gold_fracture": "同一断点形成互补橙金层状断面。",
        "same_stick_two_piece_conservation": "断后仅两段且总长度、质量守恒。",
        "sound_sync": "短促咔嚓与可见断裂同帧。",
        "foley": "一次短促咔嚓及少量碎屑落下声。",
    }


def build_contract(root: Path) -> tuple[Path, Path, Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    master = root / "box-front-master.bin"
    master.write_bytes(b"approved-box-front-master-v1")
    package = {
        "visible": True,
        "visible_faces": [
            {
                "box_id": "BOX-01",
                "face": "front",
                "visibility_state": "partially_visible",
                "master_asset": {"asset_id": "PKG-FRONT-V1", "path": str(master), "sha256": sha(master)},
                "visible_regions": ["左上品牌", "中央产品名", "主色块"],
                "observable_evidence": "正面自然部分出框，三个应见区域均与母版一致。",
                "qa_status": "approved",
            }
        ],
    }
    source_units = [
        {
            "shot_id": "S001", "source_shot_id": "SRC001",
            "source_timecode": tc(0.0, 10.0), "generation_timecode": tc(0.0, 10.0),
            "storyboard_description": "人物第一次咬食，完成咬合后闭口咀嚼。", "script_text": "第一段口播。",
            "source_performance_layers": layers(frame="SRC001-evidence.jpg"), "packaging_evidence": no_package(),
        },
        {
            "shot_id": "S002", "source_shot_id": "SRC002",
            "source_timecode": tc(10.0, 20.0), "generation_timecode": tc(0.0, 5.0),
            "storyboard_description": "人物手持方盒展示正面，保留自然遮挡。", "script_text": "第二段口播。",
            "source_performance_layers": layers(frame="SRC002-evidence.jpg"), "packaging_evidence": package,
        },
        {
            "shot_id": "S003", "source_shot_id": "SRC003",
            "source_timecode": tc(20.0, 30.0), "generation_timecode": tc(0.0, 5.0),
            "storyboard_description": "产品近景承接口播节奏。", "script_text": "第三段口播。",
            "source_performance_layers": layers(frame="SRC003-evidence.jpg"), "packaging_evidence": no_package(),
        },
    ]
    inserted_units = [
        {
            "shot_id": "S002", "inserted_shot_id": "ADD001", "generation_timecode": tc(5.0, 10.0),
            "storyboard_description": "第二次非连续咬食事件。", "script_text": "第四段口播。",
            "insertion_rationale": "原片30秒且仅一次吃食，补入第二次。", "rhythm_anchor": "卖点句后停半拍。",
            "source_reference_shot_ids": ["SRC001"], "source_reference_frame": "SRC001-eating-reference.jpg",
            "source_performance_layers": layers(inserted=True), "packaging_evidence": no_package(),
        },
        {
            "shot_id": "S003", "inserted_shot_id": "ADD002", "generation_timecode": tc(5.0, 10.0),
            "storyboard_description": "人物出镜掰开同一根黄油脆丝棒并形成第三次吃食事件。", "script_text": "第五段口播。",
            "insertion_rationale": "补足第三次非连续吃食并呈现人物掰开。", "rhythm_anchor": "酥脆卖点重音。",
            "source_reference_shot_ids": ["SRC001", "SRC003"], "source_reference_frame": "SRC003-person-break-reference.jpg",
            "source_performance_layers": layers(inserted=True), "packaging_evidence": no_package(),
        },
        {
            "shot_id": "S004", "inserted_shot_id": "ADD003", "generation_timecode": tc(0.0, 4.0),
            "storyboard_description": "无人出镜，双手单根掰开并少量掉渣。", "script_text": "【无口播，仅同步咔嚓拟音】",
            "insertion_rationale": "落实无人出镜纯手掰开硬要求。", "rhythm_anchor": "结尾质感证据位。",
            "source_reference_shot_ids": ["SRC003"], "source_reference_frame": "SRC003-hands-reference.jpg",
            "source_performance_layers": layers(inserted=True), "packaging_evidence": no_package(),
        },
    ]
    generation_map = [
        {"shot_id": "S001", "generation_timecode": tc(0.0, 10.0), "unit_ids": ["SRC001"], "source_shot_ids": ["SRC001"], "inserted_shot_ids": []},
        {"shot_id": "S002", "generation_timecode": tc(10.0, 20.0), "unit_ids": ["SRC002", "ADD001"], "source_shot_ids": ["SRC002"], "inserted_shot_ids": ["ADD001"]},
        {"shot_id": "S003", "generation_timecode": tc(20.0, 30.0), "unit_ids": ["SRC003", "ADD002"], "source_shot_ids": ["SRC003"], "inserted_shot_ids": ["ADD002"]},
        {"shot_id": "S004", "generation_timecode": tc(30.0, 34.0), "unit_ids": ["ADD003"], "source_shot_ids": [], "inserted_shot_ids": ["ADD003"]},
    ]
    eating_plan = {
        "policy": {
            "source_duration_threshold_seconds": 30, "target_event_count": 3,
            "events_are_non_contiguous": True, "one_event_is_not_multiple_images": True,
        },
        "occurrences": [
            {
                "id": "EAT-01", "event_group_id": "EAT-GROUP-01", "shot_id": "S001", "unit_id": "SRC001",
                "origin": "source", "source_shot_id": "SRC001", "generation_timecode": tc(2.0, 3.0),
                "timeline_timecode": tc(2.0, 3.0), "rhythm_anchor": "开场体验点", "script_anchor": "第一段口播",
                "required_phases": ["approach", "bite", "closed_mouth_chew", "speech_transition"], "non_contiguous_event": True,
            },
            {
                "id": "EAT-02", "event_group_id": "EAT-GROUP-02", "shot_id": "S002", "unit_id": "ADD001",
                "origin": "inserted", "inserted_shot_id": "ADD001", "generation_timecode": tc(6.0, 7.0),
                "timeline_timecode": tc(16.0, 17.0), "rhythm_anchor": "中段体验点", "script_anchor": "第四段口播",
                "required_phases": ["approach", "bite", "closed_mouth_chew"], "non_contiguous_event": True,
            },
            {
                "id": "EAT-03", "event_group_id": "EAT-GROUP-03", "shot_id": "S003", "unit_id": "ADD002",
                "origin": "inserted", "inserted_shot_id": "ADD002", "generation_timecode": tc(6.0, 7.0),
                "timeline_timecode": tc(26.0, 27.0), "rhythm_anchor": "后段体验点", "script_anchor": "第五段口播",
                "required_phases": ["fracture", "bite", "closed_mouth_chew"], "non_contiguous_event": True,
            },
        ],
    }
    break_plan = {
        "required": True,
        "required_modes": ["person_present", "hands_only_product"],
        "occurrences": [
            {
                "id": "BREAK-01", "shot_id": "S003", "unit_id": "ADD002", "mode": "person_present",
                "origin": "inserted", "inserted_shot_id": "ADD002", "generation_timecode": tc(5.2, 6.0),
                "rhythm_rationale": "人物出镜卖点重音完成脆断。", "insertion_rationale": "按口播节奏加入人物掰开。",
                "crisp_proof": crisp(),
            },
            {
                "id": "BREAK-02", "shot_id": "S004", "unit_id": "ADD003", "mode": "hands_only_product",
                "origin": "inserted", "inserted_shot_id": "ADD003", "generation_timecode": tc(1.0, 2.0),
                "rhythm_rationale": "结尾证据位完成无人出镜脆断。", "insertion_rationale": "落实纯手单根硬性镜头。",
                "crisp_proof": crisp(),
            },
        ],
    }
    locked = {
        "source_duration_seconds": 30.0, "source_units": source_units, "inserted_units": inserted_units,
        "generation_shot_map": generation_map, "eating_plan": eating_plan, "break_plan": break_plan,
    }
    locked_path = root / "locked.json"
    write(locked_path, locked)
    lock_hash = semantic_shot_map_sha256(locked)
    collections = {
        "shot_ids": ["S001", "S002", "S003", "S004"],
        "source_shot_ids": ["SRC001", "SRC002", "SRC003"],
        "inserted_shot_ids": ["ADD001", "ADD002", "ADD003"],
        "unit_ids": ["SRC001", "SRC002", "ADD001", "SRC003", "ADD002", "ADD003"],
    }
    text_handoff = {
        "schema_version": "text-handoff-v2.0", "execution_tier": "full_delivery", "branch_role": "text",
        "locked_semantic_hash": lock_hash, "shot_map_sha256": lock_hash, "status": "complete",
        "collections": collections, "completed_shot_ids": collections["shot_ids"],
        "completed_source_shot_ids": collections["source_shot_ids"],
        "completed_inserted_shot_ids": collections["inserted_shot_ids"],
        "blocked_items": [], "artifacts": [], **copy.deepcopy(locked),
    }
    image_units: list[dict[str, Any]] = []
    locked_by_id = {item.get("source_shot_id") or item.get("inserted_shot_id"): item for item in source_units + inserted_units}
    for index, unit_id in enumerate(collections["unit_ids"]):
        image_file = root / f"{unit_id}.png"
        image_file.write_bytes(f"approved-image-{unit_id}-{index}".encode())
        source = locked_by_id[unit_id]
        unit_type = "source" if unit_id.startswith("SRC") else "inserted"
        entry = {
            "unit_id": unit_id, "unit_type": unit_type, **copy.deepcopy(source),
            "approved_assets": [{
                "asset_id": f"ASSET-{unit_id}", "image_path": str(image_file), "sha256": sha(image_file),
                "width": 1080, "height": 1920, "approval_status": "user_approved",
                "responsibility": f"{unit_id} 段首批准状态",
                "user_approval": {
                    "status": "user_approved", "display_receipt_id": "gallery-test-001",
                    "approved_at": "2026-08-24T12:01:00+08:00", "asset_sha256": sha(image_file),
                },
            }],
            "qa": {
                "status": "approved", "observable_evidence": f"{unit_id} 全图和原尺寸局部均通过。",
                "six_layers_verified": True,
                "packaging_visible_faces_verified": bool(source["packaging_evidence"]["visible"]),
                "package_integration": {
                    "box_measurements": [{"box_id": "BOX1", "front_width_height_ratio": 1.0, "thickness_front_ratio": 0.30, "same_size_as_peer_boxes": True}],
                    "scene_light_match": "matched", "contact_shadow": "matched", "edge_blend": "matched",
                    "flat_cutout": False, "observable_evidence": "盒体折边、场景主光和桌面接触影连续。",
                } if source["packaging_evidence"]["visible"] else None,
            },
        }
        if unit_id == "SRC001":
            second_file = root / f"{unit_id}-bite-contact.png"
            second_file.write_bytes(b"approved-image-SRC001-bite-contact")
            entry["approved_assets"].append(
                {
                    "asset_id": "ASSET-SRC001-BITE",
                    "image_path": str(second_file),
                    "sha256": sha(second_file),
                    "width": 1080,
                    "height": 1920,
                    "approval_status": "user_approved",
                    "responsibility": "牙齿接触完成咬合的动作关键状态",
                    "user_approval": {
                        "status": "user_approved", "display_receipt_id": "gallery-test-001",
                        "approved_at": "2026-08-24T12:01:00+08:00", "asset_sha256": sha(second_file),
                    },
                }
            )
        image_units.append(entry)
    units_by_id = {item["unit_id"]: item for item in image_units}
    image_handoff = {
        "schema_version": "image-handoff-v2.1", "execution_tier": "full_delivery", "branch_role": "image",
        "locked_semantic_hash": lock_hash, "shot_map_sha256": lock_hash, "status": "ready_for_merge",
        "collections": collections, "completed_shot_ids": collections["shot_ids"],
        "completed_source_shot_ids": collections["source_shot_ids"],
        "completed_inserted_shot_ids": collections["inserted_shot_ids"],
        "blocked_items": [], "artifacts": [], "source_duration_seconds": 30.0,
        "generation_shot_map": copy.deepcopy(generation_map), "eating_plan": copy.deepcopy(eating_plan),
        "break_plan": copy.deepcopy(break_plan), "units": image_units,
        "eating_plan_review": {
            "occurrences": [
                {
                    "id": item["id"], "unit_id": item["unit_id"], "shot_id": item["shot_id"],
                    "status": "approved", "observable_evidence": f"{item['id']} 是独立非连续吃食事件。",
                    "evidence_asset_ids": [units_by_id[item["unit_id"]]["approved_assets"][0]["asset_id"]],
                }
                for item in eating_plan["occurrences"]
            ]
        },
        "break_plan_review": {
            "occurrences": [
                {
                    "id": item["id"], "unit_id": item["unit_id"], "shot_id": item["shot_id"], "mode": item["mode"],
                    "status": "approved", "observable_evidence": f"{item['id']} 断点、互补断面和少量掉渣清楚。",
                    "evidence_asset_ids": [units_by_id[item["unit_id"]]["approved_assets"][0]["asset_id"]],
                }
                for item in break_plan["occurrences"]
            ]
        },
        "controller_reply": {
            "must_inline_images": True, "may_only_report_path": False, "deliver_when_ready": True,
            "final_ready_requires_per_unit_gallery": True, "candidate_display_label": "候选/未批准",
            "gallery_unit_ids": collections["unit_ids"],
            "gallery_asset_refs": [
                {"unit_id": unit["unit_id"], "asset_id": asset["asset_id"]}
                for unit in image_units
                for asset in unit["approved_assets"]
            ],
        },
        "candidate_progress": [],
        "gallery_receipt": {
            "status": "user_approved", "display_receipt_id": "gallery-test-001",
            "displayed_at": "2026-08-24T12:00:00+08:00", "approved_at": "2026-08-24T12:01:00+08:00",
            "asset_refs": [
                {"unit_id": unit["unit_id"], "asset_id": asset["asset_id"], "sha256": asset["sha256"]}
                for unit in image_units for asset in unit["approved_assets"]
            ],
        },
    }
    text_path, image_path = root / "text.json", root / "image.json"
    write(text_path, text_handoff)
    write(image_path, image_handoff)
    return locked_path, text_path, image_path, locked, text_handoff, image_handoff


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="handoff-v2-") as temporary:
        root = Path(temporary)
        locked_path, text_path, image_path, locked, text_handoff, image_handoff = build_contract(root)
        expect(True, text_path, locked_path)
        expect(True, image_path, locked_path)

        bad = copy.deepcopy(image_handoff)
        bad["schema_version"] = "image-handoff-v1.0"
        write(image_path, bad)
        expect(False, image_path, locked_path, "v1 full-delivery")

        bad = copy.deepcopy(image_handoff)
        bad["units"] = [item for item in bad["units"] if item["unit_id"] != "ADD003"]
        bad["completed_shot_ids"] = ["S001", "S002", "S003"]
        bad["completed_inserted_shot_ids"] = ["ADD001", "ADD002"]
        bad["controller_reply"]["gallery_unit_ids"] = [item["unit_id"] for item in bad["units"]]
        write(image_path, bad)
        expect(False, image_path, locked_path, "missing=['ADD003']")

        bad = copy.deepcopy(image_handoff)
        bad["units"][0]["approved_asset"] = copy.deepcopy(bad["units"][0]["approved_assets"][0])
        write(image_path, bad)
        expect(False, image_path, locked_path, "legacy approved_asset")

        bad = copy.deepcopy(image_handoff)
        bad["units"][0]["approved_assets"] = []
        write(image_path, bad)
        expect(False, image_path, locked_path, "at least one approved target frame")

        bad = copy.deepcopy(image_handoff)
        bad["units"][1]["approved_assets"][0] = copy.deepcopy(bad["units"][0]["approved_assets"][0])
        bad["controller_reply"]["gallery_asset_refs"] = [
            {"unit_id": unit["unit_id"], "asset_id": asset["asset_id"]}
            for unit in bad["units"] for asset in unit["approved_assets"]
        ]
        write(image_path, bad)
        expect(False, image_path, locked_path, "reused across")

        bad = copy.deepcopy(image_handoff)
        bad["units"][0]["approved_assets"][0]["image_path"] = "relative/SRC001.png"
        write(image_path, bad)
        expect(False, image_path, locked_path, "absolute path")

        bad = copy.deepcopy(image_handoff)
        bad["units"][0]["approved_assets"][0]["image_path"] = str(root / "does-not-exist.png")
        write(image_path, bad)
        expect(False, image_path, locked_path, "does not exist")

        bad = copy.deepcopy(image_handoff)
        del bad["units"][0]["source_performance_layers"]["gaze"]
        write(image_path, bad)
        expect(False, image_path, locked_path, "missing six-layer")

        bad = copy.deepcopy(image_handoff)
        bad["break_plan_review"]["occurrences"].pop()
        write(image_path, bad)
        expect(False, image_path, locked_path, "every locked occurrence")

        bad = copy.deepcopy(image_handoff)
        bad["controller_reply"]["may_only_report_path"] = True
        write(image_path, bad)
        expect(False, image_path, locked_path, "may_only_report_path")

        bad = copy.deepcopy(image_handoff)
        bad["collections"]["unit_ids"][1:3] = reversed(bad["collections"]["unit_ids"][1:3])
        write(image_path, bad)
        expect(False, image_path, locked_path, "canonical order")

        bad = copy.deepcopy(text_handoff)
        bad["alignment_table"] = "自然语言对齐表"
        write(text_path, bad)
        expect(False, text_path, locked_path, "forbidden")

        # Three pictures/rows cannot impersonate three eating events: event group, unit and S must be distinct.
        bad_locked = copy.deepcopy(locked)
        for item in bad_locked["eating_plan"]["occurrences"]:
            item["event_group_id"] = "ONE-EVENT"
            item["shot_id"] = "S001"
            item["unit_id"] = "SRC001"
            item.pop("inserted_shot_id", None)
            item["origin"] = "source"
            item["source_shot_id"] = "SRC001"
        bad_locked_path = root / "bad-eating-lock.json"
        write(bad_locked_path, bad_locked)
        bad_text = copy.deepcopy(text_handoff)
        bad_text.update(copy.deepcopy(bad_locked))
        bad_hash = semantic_shot_map_sha256(bad_locked)
        bad_text["locked_semantic_hash"] = bad_text["shot_map_sha256"] = bad_hash
        write(text_path, bad_text)
        expect(False, text_path, bad_locked_path, "one eating event cannot be counted as multiple images")

        bad_locked = copy.deepcopy(locked)
        bad_locked["source_units"][1]["packaging_evidence"]["visible_faces"] = []
        bad_locked_path = root / "bad-package-lock.json"
        write(bad_locked_path, bad_locked)
        bad_text = copy.deepcopy(text_handoff)
        bad_text.update(copy.deepcopy(bad_locked))
        bad_hash = semantic_shot_map_sha256(bad_locked)
        bad_text["locked_semantic_hash"] = bad_text["shot_map_sha256"] = bad_hash
        write(text_path, bad_text)
        expect(False, text_path, bad_locked_path, "requires at least one packaging master visible face")

    print(f"FULL-DELIVERY HANDOFF V2 TESTS PASSED ({CASES} cases)")


if __name__ == "__main__":
    main()
