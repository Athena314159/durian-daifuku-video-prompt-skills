#!/usr/bin/env python3
"""Create a non-destructive v2 copy of a legacy durian-daifuku project."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from init_project import PROFILES_DIR, RELEASE_MANIFEST_PATH, TEMPLATE_DIR, load_json, seed_product_knowledge_and_assets, write_json


UNAMBIGUOUS_STATES = {"whole", "held", "plated", "pressed", "bitten"}
AMBIGUOUS_STATES = {"split", "stretched"}


def migrate(source: Path, output: Path | None = None) -> Path:
    source = source.expanduser().resolve()
    if not (source / "project.json").is_file():
        raise FileNotFoundError(f"Not a Jimeng project: {source}")
    source_project = load_json(source / "project.json")
    source_profile = source_project.get("product_profile")
    if source_profile not in {"durian-daifuku-v1", "durian-daifuku-v2"}:
        raise ValueError("Migration accepts only legacy durian-daifuku-v1 or durian-daifuku-v2 projects")
    release = load_json(RELEASE_MANIFEST_PATH)
    source_release = ((source_project.get("skill_release_lock") or {}).get("bundle_release_id"))
    if source_profile == "durian-daifuku-v2" and source_release == release.get("bundle_release_id"):
        raise ValueError("Project already uses the current release; migration is not a retry mechanism")

    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = output.expanduser().resolve() if output else source.with_name(f"{source.name}-durian-v2-{suffix}")
    if target.exists():
        raise FileExistsError(f"Migration target already exists: {target}")
    shutil.copytree(source, target)

    profile = load_json(PROFILES_DIR / "durian-daifuku-v2.json")
    write_json(target / "library" / "product_bible.json", profile)

    knowledge_path = target / "library" / "knowledge_index.json"
    knowledge = load_json(knowledge_path)
    knowledge["entries"] = [
        entry
        for entry in knowledge.get("entries") or []
        if not isinstance(entry, dict)
        or (entry.get("applies_to") or {}).get("product_profile") not in {"durian-daifuku-v1", "durian-daifuku-v2"}
    ]
    write_json(knowledge_path, knowledge)
    reference_root = target / "source" / "references" / "durian-daifuku-v2"
    if reference_root.exists():
        shutil.rmtree(reference_root)
    seeded_assets = seed_product_knowledge_and_assets(target, profile)

    product_library_path = target / "library" / "product_library.json"
    product_library = load_json(product_library_path)
    product_library["products"] = [
        {
            "id": "durian-daifuku-v2",
            "name": profile.get("name"),
            "active": True,
            "rights_cleared": False,
            "usage_scope": "internal_test",
            "profile_path": "library/product_bible.json",
            "version": profile.get("version"),
            "states": sorted((profile.get("state_profiles") or {}).keys()),
            "reference_assets": seeded_assets,
            "approved_result_assets": [],
        }
    ]
    write_json(product_library_path, product_library)

    shot_path = target / "shots" / "shot_manifest.json"
    shots = load_json(shot_path)
    ambiguous: list[dict[str, Any]] = []
    for shot in shots.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        state = shot.get("product_state") or {}
        legacy_state = state.get("state")
        state["profile"] = "durian-daifuku-v2"
        if source_profile == "durian-daifuku-v1":
            state["legacy_state"] = legacy_state
            for field in ("scale_lock", "surface_lock", "filling_lock", "endpoint_lock", "reference_roles"):
                state.pop(field, None)
            if legacy_state in AMBIGUOUS_STATES or legacy_state not in UNAMBIGUOUS_STATES:
                state["state"] = "migration_required"
                ambiguous.append({"shot_id": shot.get("id"), "legacy_state": legacy_state})
        else:
            scale_lock = state.get("scale_lock") if isinstance(state.get("scale_lock"), dict) else {}
            scale_lock.pop("pixel_plan", None)
            if scale_lock:
                state["scale_lock"] = scale_lock
        shot["product_state"] = state
        assets = shot.setdefault("asset_links", {})
        if source_profile == "durian-daifuku-v1":
            assets["product_references"] = []
        assets["approved_generation_first_frame"] = None
        for field in ("candidate_generation_first_frame", "image_generation_authorization", "image_generation_result_receipt", "scale_guide"):
            assets.pop(field, None)
        edit_chain = assets.get("edit_chain") if isinstance(assets.get("edit_chain"), dict) else {}
        face_enabled = edit_chain.get("face_edit_enabled") is True
        product_enabled = bool(assets.get("product_references"))
        edit_chain.update(
            {
                "atomic_identity_product_required": bool(face_enabled and product_enabled),
                "retry_origin_policy": "exact_original_source_only",
                "partial_candidate_policy": "diagnostic_only_never_reuse",
                "approved_first_frame_review": "migration_reauthorization_required",
                "notes": "每次从同编号原始 source_first_frame 发起；同时需要换身份与换产品时必须同轮原子执行，任一失败整张拒绝并回原图。",
            }
        )
        assets["edit_chain"] = edit_chain
        for unit_key in ("source_units", "inserted_units"):
            for unit in shot.get(unit_key) or []:
                if isinstance(unit, dict):
                    unit["delivery_asset_ids"] = []
    write_json(shot_path, shots)

    project_path = target / "project.json"
    project = load_json(project_path)
    project["product_profile"] = "durian-daifuku-v2"
    project["status"] = "draft"
    project["skill_release_lock"] = {
        "bundle_release_id": release["bundle_release_id"],
        "prompt_authoring_contract": release["prompt_authoring_contract"],
        "auto_upgrade": False,
    }
    project["migration_requirements"] = {
        "requires_manual_shot_map_rebuild": bool(ambiguous),
        "requires_generation_reauthorization": True,
        "reason": "Rebind ambiguous v1 states when present, rebuild every pixel plan, and authorize all new image requests from the exact original frame under the current release.",
        "ambiguous_legacy_states": ambiguous,
        "source_project": str(source),
        "source_release": source_release,
        "target_release": release["bundle_release_id"],
    }
    write_json(project_path, project)

    workflow_path = target / "planning" / "workflow_state.json"
    workflow = load_json(workflow_path)
    workflow["prompt_delivery"] = {
        "authorized": False,
        "reason": "Legacy v1 product contract and compile evidence were invalidated by explicit v2 migration.",
    }
    workflow["skill_versions"] = {
        **(workflow.get("skill_versions") or {}),
        "bundle_release_id": release["bundle_release_id"],
        "prompt_authoring_contract": release["prompt_authoring_contract"],
        "product_profile": "durian-daifuku-v2",
    }
    write_json(workflow_path, workflow)

    archive = target / "legacy-release-artifacts" / suffix
    archive.mkdir(parents=True, exist_ok=True)
    stale_paths = (
        Path("prompts"),
        Path("review/prompt_delivery_receipt.json"),
        Path("review/image-generation-requests"),
        Path("review/scale-guides"),
        Path("review/candidates"),
        Path("review/approved"),
        Path("candidates"),
        Path("diagnostic-failures"),
        Path("approved"),
        Path("planning/asset_reuse_plan.json"),
    )
    for relative in stale_paths:
        current = target / relative
        if current.exists():
            destination = archive / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(destination))
    (target / "prompts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(TEMPLATE_DIR / "planning" / "asset_reuse_plan.json", target / "planning" / "asset_reuse_plan.json")
    fresh_reuse_plan = load_json(target / "planning" / "asset_reuse_plan.json")
    fresh_reuse_plan["contract_binding"] = {
        "bundle_release_id": release["bundle_release_id"],
        "prompt_authoring_contract": release["prompt_authoring_contract"],
        "product_profile": "durian-daifuku-v2",
    }
    write_json(target / "planning" / "asset_reuse_plan.json", fresh_reuse_plan)
    write_json(
        target / "review" / "migration_cleanup_receipt.json",
        {
            "schema_version": "video-remix-migration-cleanup-v1.0",
            "status": "legacy_execution_artifacts_archived",
            "source_project": str(source),
            "source_release": source_release,
            "target_release": release["bundle_release_id"],
            "archive": str(archive),
            "archived_paths": [str(path) for path in stale_paths],
            "active_prompts_cleared": True,
            "active_generation_receipts_cleared": True,
            "active_approved_frames_unbound": True,
            "active_asset_reuse_plan_reset": True,
        },
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a non-destructive durian-daifuku-v2 project copy.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = migrate(args.project_dir, args.output)
    print(json.dumps({"status": "migrated_requires_manual_rebuild", "project_dir": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
