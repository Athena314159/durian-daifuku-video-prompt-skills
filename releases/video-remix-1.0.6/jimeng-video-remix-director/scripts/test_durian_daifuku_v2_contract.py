#!/usr/bin/env python3
"""Regression tests for the durian-daifuku-v2 integration contract."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from init_project import initialize_project, load_json, write_json  # noqa: E402
from migrate_durian_daifuku_v2 import migrate  # noqa: E402
from pipeline import compile_shot, validate_durian_daifuku_v2_shot  # noqa: E402


def role(asset: dict) -> dict:
    return {
        "asset_id": asset["id"],
        "role": asset["role"],
        "allowed_inheritance": asset["allowed_inheritance"],
        "forbidden_inheritance": asset["forbidden_inheritance"],
    }


def opening_state(product: dict) -> dict:
    assets = {item["id"]: item for item in product["reference_assets"]}
    selected = [assets["DF2-SURFACE-01"], assets["DF2-OPENING-SEED-01"]]
    return {
        "profile": "durian-daifuku-v2",
        "state": "opening_window_seed",
        "count": 1,
        "packaging": "none",
        "shot_specific_traits": ["中央首次微露馅后立即停止"],
        "scale_lock": {
            "mode": "physical_consistency",
            "source_scale_role": "pose_only_incompatible_scale",
            "anchor": {
                "type": "index_finger_mid",
                "expected_ratio": [3.5, 4.0],
                "evidence": "同景深接触食指中段可见，完整可重建宽度可直接复核",
            },
        },
        "surface_lock": {
            "rice_flour_haze": True,
            "visible_in_oblique_light": True,
            "individually_resolvable_particles": False,
        },
        "filling_lock": {
            "continuous_puree_ratio": 0.9,
            "countable_lumps": False,
            "holes_or_honeycomb": False,
            "stringing": False,
        },
        "endpoint_lock": {
            "terminal_state": "opening_window_seed",
            "single_endpoint": True,
            "max_visible_filling_area_ratio": 0.05,
            "piece_air_gap_cm": 0,
        },
        "reference_roles": [role(item) for item in selected],
    }


def opening_shot(product: dict) -> dict:
    state = opening_state(product)
    asset_paths = {
        item["id"]: item.get("target_path") or item.get("path")
        for item in product["reference_assets"]
    }
    return {
        "id": "S001",
        "title": "首次微露馅",
        "visual_type": "product_showcase",
        "narrative_role": "visual_proof",
        "purpose": "证明冰皮刚建立极小撕口的真实状态",
        "script_segment_ids": [],
        "scene_rationale": "沿用原片双手与场景",
        "source_facts": ["双手持同一颗产品"],
        "source_locks": ["人物、手、场景、机位不变"],
        "allowed_changes": ["只替换主体食品"],
        "source_units": [],
        "inserted_units": [],
        "timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
        "scene": {"location": "原片桌边", "background": ["原片背景"], "foreground": ["双手和产品"]},
        "character": {"present": False, "hands_only": True},
        "emotion": {},
        "action_beats": [],
        "product_state": state,
        "camera": {"shot_size": "近景", "angle": "平视", "movement": "固定", "focus": "小开口", "lens_feel": "真实手机"},
        "lighting": {"source": "原片侧向柔光", "temperature": "warm", "notes": ["粉雾层可见"]},
        "audio": {"delivery_mode": "silent", "script_text": "", "delivery_rationale": "纯产品证据", "voice_direction": "", "foley": ["轻微黏糯受力声"], "music": "无"},
        "hard_constraints": ["首次微露馅后动作立即停止"],
        "prohibited": ["大开口", "两半", "颗粒孔洞内馅"],
        "continuity": ["同一颗约7厘米产品保持质量守恒"],
        "asset_links": {
            "product_references": [asset_paths["DF2-SURFACE-01"], asset_paths["DF2-OPENING-SEED-01"]],
        },
        "risk": {"level": "medium", "reasons": ["小开口易过冲"]},
    }


def assert_code(issues: list[dict], code: str) -> None:
    assert any(issue.get("code") == code for issue in issues), (code, issues)


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        project_dir = initialize_project(
            "daifuku-v2-test",
            root,
            "durian-daifuku-v2",
            "ugc-food-review-v1",
            execution_tier="prompt_only",
        )
        product = load_json(project_dir / "library/product_bible.json")
        knowledge = load_json(project_dir / "library/knowledge_index.json")
        assert product["profile_id"] == "durian-daifuku-v2"
        assert product["version"] == 2
        assert any(entry.get("id") == "KB-DF2-BASE-001" for entry in knowledge["entries"])
        assert any(entry.get("id") == "KB-DF2-OPENING-SEED-001" for entry in knowledge["entries"])
        image_entries = [entry for entry in knowledge["entries"] if entry.get("type") == "image"]
        assert image_entries and all((project_dir / entry["path"]).is_file() for entry in image_entries)
        assert all((entry.get("applies_to") or {}).get("product_profile") == "durian-daifuku-v2" for entry in knowledge["entries"])

        shot = opening_shot(product)
        issues: list[dict] = []
        validate_durian_daifuku_v2_shot(project_dir, product, shot, issues, "shot")
        assert not issues, issues

        overshoot = copy.deepcopy(shot)
        overshoot["product_state"]["endpoint_lock"]["max_visible_filling_area_ratio"] = 0.2
        overshoot["product_state"]["endpoint_lock"]["piece_air_gap_cm"] = 1
        issues = []
        validate_durian_daifuku_v2_shot(project_dir, product, overshoot, issues, "shot")
        assert_code(issues, "DAIFUKU_OPENING_OVERSHOOT")

        powderless = copy.deepcopy(shot)
        powderless["product_state"]["surface_lock"]["rice_flour_haze"] = False
        issues = []
        validate_durian_daifuku_v2_shot(project_dir, product, powderless, issues, "shot")
        assert_code(issues, "DAIFUKU_POWDER_SURFACE_INVALID")

        lumpy = copy.deepcopy(shot)
        lumpy["product_state"]["filling_lock"]["countable_lumps"] = True
        issues = []
        validate_durian_daifuku_v2_shot(project_dir, product, lumpy, issues, "shot")
        assert_code(issues, "DAIFUKU_FILLING_TEXTURE_INVALID")

        polluted = copy.deepcopy(shot)
        opened = next(item for item in product["reference_assets"] if item["id"] == "DF2-OPENED-TEXTURE-01")
        polluted["product_state"]["reference_roles"].append(role(opened))
        polluted["asset_links"]["product_references"].append(opened.get("target_path") or opened.get("path"))
        issues = []
        validate_durian_daifuku_v2_shot(project_dir, product, polluted, issues, "shot")
        assert_code(issues, "DAIFUKU_REFERENCE_STATE_POLLUTION")

        legacy = copy.deepcopy(shot)
        legacy["hard_constraints"].append("形成2至4条、3至6厘米连接带")
        issues = []
        validate_durian_daifuku_v2_shot(project_dir, product, legacy, issues, "shot")
        assert_code(issues, "DAIFUKU_LEGACY_CONTRACT_CONFLICT")

        project = load_json(project_dir / "project.json")
        bundle = {
            "project": project,
            "product": product,
            "style": load_json(project_dir / "library/style_bible.json"),
            "story": {"break_plan": {"occurrences": []}},
            "corrections": {"rules": []},
            "knowledge": knowledge,
        }
        markdown, metadata = compile_shot(bundle, shot)
        assert metadata["product_profile"] == "durian-daifuku-v2"
        for token in ("约7厘米", "细糯米粉雾层", "连续果泥", "opening_window_seed", "到达终点后立即停止"):
            assert token in markdown, token
        for obsolete in ("沙沙颗粒感", "2至4条短而宽", "3至6厘米短距离连接"):
            assert obsolete not in markdown, obsolete

        legacy_dir = initialize_project(
            "daifuku-v1-test",
            root,
            "durian-daifuku-v1",
            "ugc-food-review-v1",
            execution_tier="prompt_only",
        )
        legacy_shots = load_json(legacy_dir / "shots/shot_manifest.json")
        legacy_shots["shots"] = [{"id": "S001", "product_state": {"profile": "durian-daifuku-v1", "state": "stretched"}, "asset_links": {"product_references": []}}]
        write_json(legacy_dir / "shots/shot_manifest.json", legacy_shots)
        (legacy_dir / "prompts" / "S001.md").write_text("legacy prompt\n", encoding="utf-8")
        migrated = migrate(legacy_dir, root / "migrated-v2")
        assert load_json(legacy_dir / "project.json")["product_profile"] == "durian-daifuku-v1"
        assert load_json(migrated / "project.json")["product_profile"] == "durian-daifuku-v2"
        migrated_state = load_json(migrated / "shots/shot_manifest.json")["shots"][0]["product_state"]
        assert migrated_state["state"] == "migration_required"
        assert (migrated / "legacy-release-artifacts").is_dir()
        assert not list((migrated / "prompts").glob("*.md"))

        legacy_v2_dir = initialize_project(
            "daifuku-v2-release-test",
            root,
            "durian-daifuku-v2",
            "ugc-food-review-v1",
            execution_tier="first_frame_only",
        )
        legacy_v2_project = load_json(legacy_v2_dir / "project.json")
        legacy_v2_project["skill_release_lock"]["bundle_release_id"] = "video-remix-1.0.5"
        write_json(legacy_v2_dir / "project.json", legacy_v2_project)
        legacy_v2_manifest = load_json(legacy_v2_dir / "shots" / "shot_manifest.json")
        old_shot = opening_shot(load_json(legacy_v2_dir / "library" / "product_bible.json"))
        old_shot["asset_links"]["approved_generation_first_frame"] = "review/approved/old.png"
        legacy_v2_manifest["shots"] = [old_shot]
        write_json(legacy_v2_dir / "shots" / "shot_manifest.json", legacy_v2_manifest)
        (legacy_v2_dir / "prompts" / "S001.md").write_text("old prompt\n", encoding="utf-8")
        old_candidate = legacy_v2_dir / "review" / "candidates" / "old.png"
        old_candidate.parent.mkdir(parents=True, exist_ok=True)
        old_candidate.write_bytes(b"old candidate")
        migrated_v2 = migrate(legacy_v2_dir, root / "migrated-v2-release")
        assert load_json(legacy_v2_dir / "project.json")["skill_release_lock"]["bundle_release_id"] == "video-remix-1.0.5"
        migrated_v2_project = load_json(migrated_v2 / "project.json")
        assert migrated_v2_project["skill_release_lock"]["bundle_release_id"] == "video-remix-1.0.6"
        migrated_v2_shot = load_json(migrated_v2 / "shots" / "shot_manifest.json")["shots"][0]
        assert migrated_v2_shot["asset_links"]["approved_generation_first_frame"] is None
        assert not (migrated_v2 / "review" / "candidates").exists()
        cleanup = load_json(migrated_v2 / "review" / "migration_cleanup_receipt.json")
        assert cleanup["active_prompts_cleared"] is True
        assert cleanup["active_asset_reuse_plan_reset"] is True
        reuse = load_json(migrated_v2 / "planning" / "asset_reuse_plan.json")
        assert reuse["contract_binding"]["bundle_release_id"] == "video-remix-1.0.6"

    print(json.dumps({"status": "ok", "contract": "durian-daifuku-v2"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
