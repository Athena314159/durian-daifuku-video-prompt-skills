#!/usr/bin/env python3
"""Focused regressions for the canonical SRC/ADD and product-event contract."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path

from init_project import initialize_project
from migrate_project_v1_1 import migrate
from pipeline import (
    normalized_prompt_length_contract,
    validate_break_plan,
    validate_eating_plan,
    validate_source_shot_contract,
)


LAYER_KEYS = (
    "emotion_trigger",
    "gaze",
    "facial_microreaction",
    "body_hand_preparation",
    "breath_pause",
    "voice_speech",
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def issue_codes(issues: list[dict]) -> set[str]:
    return {str(issue.get("code")) for issue in issues}


def six_layers() -> dict:
    return {
        key: {
            "status": "not_applicable",
            "source_timecode": None,
            "source_reference_frame": None,
            "observable_evidence": "该原片单元没有可归属于本层的人物表演事实。",
            "confidence": 1.0,
            "gap_reason": None,
        }
        for key in LAYER_KEYS
    }


def source_unit(source_id: str, start: float, end: float, frame: Path, asset_id: str) -> dict:
    duration = end - start
    return {
        "source_shot_id": source_id,
        "source_timecode": {"start": start, "end": end, "duration": duration},
        "generation_timecode": {"start": start, "end": end, "duration": duration},
        "storyboard_description": f"{source_id} 原片可见构图、动作与节奏。",
        "script_text": "无",
        "source_first_frame": str(frame),
        "delivery_asset_ids": [asset_id],
        "source_performance_layers": six_layers(),
    }


def test_source_and_storyboard_contract(root: Path) -> None:
    frames = []
    for index in range(1, 5):
        frame = root / f"approved-{index}.jpg"
        frame.write_bytes(f"approved-{index}".encode())
        frames.append(frame)
    source = {
        "duration": 4.0,
        "frame_rate": 30,
        "source_shots": [
            {
                "id": "SRC001",
                "start_frame": 0,
                "end_frame": 60,
                "timecode": {"start": 0.0, "end": 2.0, "duration": 2.0},
                "storyboard_description": "第一原片原子分镜",
            },
            {
                "id": "SRC002",
                "start_frame": 60,
                "end_frame": 120,
                "timecode": {"start": 2.0, "end": 4.0, "duration": 2.0},
                "storyboard_description": "第二原片原子分镜",
            },
        ],
    }
    first = source_unit("SRC001", 0.0, 2.0, frames[0], "A1")
    first["delivery_asset_ids"] = ["A1", "A1B"]
    first["delivery_asset_roles"] = {
        "A1": "段首动作状态",
        "A1B": "手部完成动作的结果状态",
    }
    second = source_unit("SRC002", 2.0, 4.0, frames[1], "A2")
    inserted = {
        "inserted_shot_id": "ADD001",
        "generation_timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
        "storyboard_description": "按口播节奏新增的独立产品证明镜。",
        "script_text": "无",
        "insertion_rationale": "原片没有这项目标产品证明，因此新增。",
        "rhythm_anchor": "落在 SRC002 结束后的自然切点。",
        "source_reference_shot_ids": ["SRC002"],
        "source_reference_frame": str(frames[1]),
        "delivery_asset_ids": ["A3"],
        "source_performance_layers": six_layers(),
    }
    shots = [
        {
            "id": "S001",
            "timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
            "merge_reason": "两个相邻短 SRC 合并连续生成，但卡片和批准图各自独立。",
            "source_units": [first, second],
            "inserted_units": [],
        },
        {
            "id": "S002",
            "timecode": {"start": 4.0, "end": 8.0, "duration": 4.0},
            "source_units": [],
            "inserted_units": [inserted],
        },
    ]
    reuse = {
        "inventory": [
            {"asset_id": "A1", "path": str(frames[0]), "sha256": sha(frames[0]), "approval_status": "approved", "source_shot_ids": ["SRC001"], "responsibility": "段首动作状态"},
            {"asset_id": "A1B", "path": str(frames[3]), "sha256": sha(frames[3]), "approval_status": "approved", "source_shot_ids": ["SRC001"], "responsibility": "手部完成动作的结果状态"},
            {"asset_id": "A2", "path": str(frames[1]), "sha256": sha(frames[1]), "approval_status": "approved", "source_shot_ids": ["SRC002"], "responsibility": "第二原子镜头状态"},
            {"asset_id": "A3", "path": str(frames[2]), "sha256": sha(frames[2]), "approval_status": "approved", "inserted_shot_ids": ["ADD001"], "responsibility": "新增镜头状态"},
        ],
        "shot_decisions": [
            {"shot_id": "S001", "selected_asset_ids": ["A1", "A1B", "A2"]},
            {"shot_id": "S002", "selected_asset_ids": ["A3"]},
        ],
    }
    project = {
        "project_rules": {
            "preserve_every_source_shot": True,
            "require_frame_accurate_source_timeline": True,
            "require_at_least_one_approved_image_per_source_shot": True,
            "require_at_least_one_approved_image_per_inserted_shot": True,
            "require_structured_six_layer_evidence": True,
            "minimum_generation_clip_seconds": 4.0,
        }
    }
    issues: list[dict] = []
    validate_source_shot_contract(root, project, source, shots, reuse, issues)
    assert not issues, issues

    zero_images = copy.deepcopy(shots)
    zero_images[0]["source_units"][0]["delivery_asset_ids"] = []
    issues = []
    validate_source_shot_contract(root, project, source, zero_images, reuse, issues)
    assert "SOURCE_SHOT_APPROVED_IMAGE_MISSING" in issue_codes(issues)

    cross_unit_reuse = copy.deepcopy(shots)
    cross_unit_reuse[0]["source_units"][1]["delivery_asset_ids"] = ["A1"]
    issues = []
    validate_source_shot_contract(root, project, source, cross_unit_reuse, reuse, issues)
    assert "DELIVERY_FRAME_DUPLICATED" in issue_codes(issues)

    frame_gap = copy.deepcopy(source)
    frame_gap["source_shots"][1]["start_frame"] = 61
    issues = []
    validate_source_shot_contract(root, project, frame_gap, shots, reuse, issues)
    assert "SOURCE_FRAME_TIMELINE_GAP" in issue_codes(issues)

    time_gap = copy.deepcopy(source)
    time_gap["source_shots"][1]["timecode"] = {"start": 2.02, "end": 4.0, "duration": 1.98}
    issues = []
    validate_source_shot_contract(root, project, time_gap, shots, reuse, issues)
    assert "SOURCE_TIMELINE_GAP" in issue_codes(issues)

    not_visible = copy.deepcopy(shots)
    layer = not_visible[0]["source_units"][0]["source_performance_layers"]["gaze"]
    layer.update(
        {
            "status": "not_visible",
            "source_timecode": {"start": 0.0, "end": 2.0, "duration": 2.0},
            "source_reference_frame": str(frames[0]),
            "observable_evidence": "人物眼睛完全位于画外，无法观察视线方向。",
            "confidence": 1.0,
            "gap_reason": "该 SRC 只拍到手和产品，眼睛不在画面内。",
        }
    )
    issues = []
    validate_source_shot_contract(root, project, source, not_visible, reuse, issues)
    assert not issues, issues
    layer["gap_reason"] = None
    issues = []
    validate_source_shot_contract(root, project, source, not_visible, reuse, issues)
    assert "SIX_LAYER_NOT_VISIBLE_REASON_MISSING" in issue_codes(issues)


def eating_occurrence(identifier: str, shot_id: str, origin: str, unit_id: str) -> dict:
    value = {
        "id": identifier,
        "shot_id": shot_id,
        "origin": origin,
        "generation_timecode": {"start": 0.5, "end": 1.5, "duration": 1.0},
        "rhythm_rationale": "与前后吃食事件之间保留一个非吃食卖点镜。",
        "source_evidence": ["原片可见张口、咬合和产品离嘴"] if origin == "source" else [],
        "insertion_rationale": "原片仅有两次，只补足缺少的一次。" if origin == "inserted" else None,
        "appetite_evidence": {
            "bite_readability": "牙齿接触和产品离嘴边界清楚",
            "crisp_sound": "一次短促咔嚓",
            "product_state_change": "同一根形成自然咬口并缩短",
            "source_performance_basis": "继承参考 SRC 的送入口和头部节奏",
        },
        "visible_swallow_required": False,
        "post_bite_reaction_required": False,
        "speech_after_bite": {"enabled": False},
    }
    if origin == "source":
        value["source_shot_id"] = unit_id
    else:
        value["inserted_shot_id"] = unit_id
    return value


def test_eating_event_contract() -> None:
    full = {"start": 0.0, "end": 4.0, "duration": 4.0}
    shots = [{"id": f"S{index:03d}", "source_units": [], "inserted_units": []} for index in range(1, 6)]
    shots[0]["source_units"] = [{"source_shot_id": "SRC001", "generation_timecode": full}]
    shots[2]["source_units"] = [{"source_shot_id": "SRC002", "generation_timecode": full}]
    shots[4]["inserted_units"] = [{"inserted_shot_id": "ADD001", "generation_timecode": full}]
    story = {
        "eating_plan": {
            "source_duration_seconds": 30.0,
            "source_eating_occurrence_count": 2,
            "inserted_eating_occurrence_count": 1,
            "target_eating_occurrence_count": 3,
            "occurrences": [
                eating_occurrence("E001", "S001", "source", "SRC001"),
                eating_occurrence("E002", "S003", "source", "SRC002"),
                eating_occurrence("E003", "S005", "inserted", "ADD001"),
            ],
        }
    }
    project = {
        "project_rules": {
            "minimum_eating_occurrences_when_source_duration_gte_30": 3,
            "eating_occurrences_must_be_non_contiguous": True,
            "require_visible_swallow_or_post_bite_reaction": False,
        }
    }
    issues: list[dict] = []
    validate_eating_plan(project, {"duration": 30.0}, story, shots, issues)
    assert not issues, issues
    assert len(story["eating_plan"]["occurrences"]) == 3

    extra = copy.deepcopy(story)
    extra["eating_plan"]["inserted_eating_occurrence_count"] = 2
    issues = []
    validate_eating_plan(project, {"duration": 30.0}, extra, shots, issues)
    assert "EATING_INSERT_COUNT_MISMATCH" in issue_codes(issues)

    contiguous = copy.deepcopy(story)
    contiguous["eating_plan"]["occurrences"][1]["shot_id"] = "S002"
    contiguous_shots = copy.deepcopy(shots)
    contiguous_shots[1]["source_units"] = [{"source_shot_id": "SRC002", "generation_timecode": full}]
    issues = []
    validate_eating_plan(project, {"duration": 30.0}, contiguous, contiguous_shots, issues)
    assert "EATING_OCCURRENCES_CONTIGUOUS" in issue_codes(issues)

    unsupported = copy.deepcopy(story)
    unsupported["eating_plan"]["occurrences"][0]["visible_swallow_required"] = True
    issues = []
    validate_eating_plan(project, {"duration": 30.0}, unsupported, shots, issues)
    assert "UNSUPPORTED_SWALLOW_OR_REACTION" in issue_codes(issues)


def break_shot(shot_id: str, inserted_id: str, beat_id: str, hands_only: bool) -> dict:
    visual_type = "product_showcase" if hands_only else "person_product_showcase"
    return {
        "id": shot_id,
        "visual_type": visual_type,
        "character": {"present": not hands_only, "hands_only": hands_only},
        "product_state": {"state": "breaking", "count": 1},
        "source_units": [],
        "inserted_units": [
            {
                "inserted_shot_id": inserted_id,
                "generation_timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
                "storyboard_description": "双手掰断同一根黄油脆丝棒，少量掉渣，并展示两段互补橙金断面。",
            }
        ],
        "action_beats": [
            {
                "id": beat_id,
                "start": 0.5,
                "end": 2.0,
                "action": "双手对同一根脆丝棒反向施力并一次掰断，断点落下少量碎屑。",
                "product_change": "同一根变成两段长度守恒、轮廓互补的橙金断面。",
                "foley_cue": "断裂出现同一帧同步一声短促咔嚓与克制掉渣声。",
            }
        ],
        "audio": {"foley": ["断裂帧同步一声咔嚓", "少量碎屑落下声"]},
    }


def break_occurrence(identifier: str, shot_id: str, inserted_id: str, beat_id: str, mode: str) -> dict:
    return {
        "id": identifier,
        "shot_id": shot_id,
        "mode": mode,
        "origin": "inserted",
        "inserted_shot_id": inserted_id,
        "action_beat_id": beat_id,
        "generation_timecode": {"start": 0.8, "end": 1.4, "duration": 0.6},
        "rhythm_rationale": "在对应卖点句后停半拍，让断裂声成为节奏重音。",
        "insertion_rationale": "为目标产品增加有节奏的真实酥脆证明。",
        "crisp_proof": {
            "action_beat_id": beat_id,
            "single_snap": True,
            "fracture_visible": True,
            "material_conservation_locked": True,
            "crumbs": {"minimum": 3, "maximum": 8},
            "foley": "一次短促咔嚓与少量碎屑声",
            "complementary_orange_gold_fracture": "同一断点形成两块橙金互补断面。",
            "same_stick_two_piece_conservation": "同一根断后只有两段且总长度守恒。",
            "sound_sync": "咔嚓与断裂出现严格同帧。",
        },
    }


def test_break_event_contract() -> None:
    project = {"project_name": "黄油脆丝棒", "product_profile": "butter-crisp-v1", "project_rules": {}}
    product = {
        "name": "达尔顿黄油脆丝棒",
        "profile_id": "butter-crisp-v1",
        "break_physics": {"crumb_count_minimum": 3, "crumb_count_maximum": 8},
    }
    shots = [break_shot("S001", "ADD101", "AB-PERSON", False), break_shot("S003", "ADD103", "AB-HANDS", True)]
    story = {
        "break_plan": {
            "occurrences": [
                break_occurrence("B001", "S001", "ADD101", "AB-PERSON", "person_present"),
                break_occurrence("B002", "S003", "ADD103", "AB-HANDS", "hands_only_product"),
            ]
        }
    }
    issues: list[dict] = []
    validate_break_plan(project, product, story, shots, issues)
    assert not issues, issues

    no_person = copy.deepcopy(story)
    no_person["break_plan"]["occurrences"] = no_person["break_plan"]["occurrences"][1:]
    issues = []
    validate_break_plan(project, product, no_person, shots, issues)
    assert "PERSON_PRESENT_BREAK_SHOWCASE_MISSING" in issue_codes(issues)

    zero_crumbs = copy.deepcopy(story)
    zero_crumbs["break_plan"]["occurrences"][0]["crisp_proof"]["crumbs"] = {"minimum": 0, "maximum": 0}
    issues = []
    validate_break_plan(project, product, zero_crumbs, shots, issues)
    assert "CRUMB_RANGE_MISSING" in issue_codes(issues)

    metadata_only = copy.deepcopy(story)
    metadata_only["break_plan"]["occurrences"][0].pop("action_beat_id")
    issues = []
    validate_break_plan(project, product, metadata_only, shots, issues)
    assert "BREAK_ACTION_BEAT_BINDING_MISSING" in issue_codes(issues)

    two_sticks = copy.deepcopy(shots)
    two_sticks[1]["product_state"]["count"] = 2
    issues = []
    validate_break_plan(project, product, story, two_sticks, issues)
    assert "HANDS_ONLY_BREAK_SINGLE_STICK_REQUIRED" in issue_codes(issues)


def test_init_and_migration_contract(root: Path) -> None:
    disabled = initialize_project(
        name="黄油脆丝棒默认长度关闭",
        output=root,
        product_profile="butter-crisp-v1",
        style_profile="ugc-food-review-v1",
        project_id="butter-disabled",
    )
    disabled_project = read_json(disabled / "project.json")
    assert disabled_project["prompt_length_contract"] == {
        "enabled": False,
        "minimum_non_whitespace_characters": 0,
        "maximum_non_whitespace_characters": 0,
    }
    assert disabled_project["project_rules"]["require_hands_only_break_showcase"] is True
    assert disabled_project["project_rules"]["require_person_present_break_showcase"] is True
    assert disabled_project["project_rules"]["packaging_visible"] is True
    assert read_json(disabled / "library" / "product_bible.json")["profile_id"] == "butter-crisp-v1"

    enabled = initialize_project(
        name="用户自定长度",
        output=root,
        product_profile="butter-crisp-v1",
        style_profile="ugc-food-review-v1",
        project_id="butter-enabled",
        prompt_length_enabled=True,
        prompt_length_minimum=1200,
        prompt_length_maximum=1800,
    )
    assert read_json(enabled / "project.json")["prompt_length_contract"] == {
        "enabled": True,
        "minimum_non_whitespace_characters": 1200,
        "maximum_non_whitespace_characters": 1800,
    }
    assert normalized_prompt_length_contract(
        {"prompt_length_contract": {"enabled": True, "minimum_non_whitespace_characters": 0, "maximum_non_whitespace_characters": 0}}
    ) == {
        "enabled": True,
        "minimum_non_whitespace_characters": 3000,
        "maximum_non_whitespace_characters": 4000,
    }

    legacy = disabled
    project = read_json(legacy / "project.json")
    project["project_rules"].pop("require_break_action_beat_binding", None)
    project["project_rules"]["custom_client_rule"] = "keep-me"
    project["prompt_length_contract"] = {
        "enabled": False,
        "minimum_non_whitespace_characters": 3000,
        "maximum_non_whitespace_characters": 4000,
    }
    write_json(legacy / "project.json", project)
    story = read_json(legacy / "planning" / "story_plan.json")
    story["subtitle_script"].update({"provided_by_user": True, "text": "用户已确认口播", "effective_characters": 7})
    story["custom_story_field"] = {"keep": True}
    write_json(legacy / "planning" / "story_plan.json", story)
    manifest = read_json(legacy / "shots" / "shot_manifest.json")
    manifest["shots"] = [
        {
            "id": "S001",
            "asset_links": {"approved_generation_first_frame": "source/already-approved.jpg"},
            "audio": {"script_text": "用户已确认口播", "delivery_mode": "voiceover"},
        }
    ]
    write_json(legacy / "shots" / "shot_manifest.json", manifest)
    result = migrate(legacy)
    migrated_project = read_json(legacy / "project.json")
    migrated_story = read_json(legacy / "planning" / "story_plan.json")
    migrated_manifest = read_json(legacy / "shots" / "shot_manifest.json")
    assert migrated_project["project_rules"]["require_break_action_beat_binding"] is True
    assert migrated_project["project_rules"]["custom_client_rule"] == "keep-me"
    assert migrated_project["prompt_length_contract"]["enabled"] is False
    assert migrated_project["prompt_length_contract"]["minimum_non_whitespace_characters"] == 0
    assert migrated_story["subtitle_script"]["text"] == "用户已确认口播"
    assert migrated_story["subtitle_script"]["provided_by_user"] is True
    assert migrated_story["custom_story_field"] == {"keep": True}
    assert migrated_manifest["shots"][0]["asset_links"]["approved_generation_first_frame"] == "source/already-approved.jpg"
    assert result["requires_manual_shot_map_rebuild"] is True
    assert "Cannot recover canonical SRC/ADD" in str(result["rebuild_reason"])
    assert (Path(result["backup_dir"]) / "planning" / "story_plan.json").is_file()

    recoverable = initialize_project(
        name="可恢复项目",
        output=root,
        product_profile="butter-crisp-v1",
        style_profile="ugc-food-review-v1",
        project_id="recoverable",
    )
    source = read_json(recoverable / "source" / "source_manifest.json")
    source.update(
        {
            "duration": 4.0,
            "frame_rate": 30,
            "source_shots": [
                {
                    "id": "SRC001",
                    "start_frame": 0,
                    "end_frame": 120,
                    "timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
                    "storyboard_description": "完整原片分镜",
                }
            ],
        }
    )
    write_json(recoverable / "source" / "source_manifest.json", source)
    manifest = read_json(recoverable / "shots" / "shot_manifest.json")
    manifest["shots"] = [{"id": "S001", "source_units": [{"source_shot_id": "SRC001"}], "inserted_units": []}]
    write_json(recoverable / "shots" / "shot_manifest.json", manifest)
    recovered_result = migrate(recoverable)
    assert recovered_result["requires_manual_shot_map_rebuild"] is False
    assert read_json(recoverable / "project.json")["migration_requirements"]["requires_manual_shot_map_rebuild"] is False


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pipeline-contract-hardening-") as temporary:
        root = Path(temporary)
        test_source_and_storyboard_contract(root)
        test_eating_event_contract()
        test_break_event_contract()
        test_init_and_migration_contract(root)
    print("PIPELINE CONTRACT HARDENING TESTS PASSED: SRC/ADD, timeline, six-layer, eating, break, init, migration")


if __name__ == "__main__":
    main()
