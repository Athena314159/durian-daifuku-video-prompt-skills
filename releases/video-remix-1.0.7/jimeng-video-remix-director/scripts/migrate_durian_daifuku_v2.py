#!/usr/bin/env python3
"""Create a non-destructive v2 copy of a legacy durian-daifuku project."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from init_project import PROFILES_DIR, RELEASE_MANIFEST_PATH, TEMPLATE_DIR, load_json, seed_product_knowledge_and_assets, write_json


UNAMBIGUOUS_STATES = {"whole", "held", "plated", "pressed", "bitten"}
AMBIGUOUS_STATES = {"split", "stretched"}
LEGACY_PROFILE = "durian-daifuku-v1"
CURRENT_PROFILE = "durian-daifuku-v2"
LEGACY_RELEASE_RE = re.compile(r"video-remix-\d+\.\d+\.\d+")
LEGACY_AUDIT_ALLOWLIST = {
    Path("project.json"),
    Path("library/product_bible.json"),
    Path("review/migration_cleanup_receipt.json"),
}


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def rewrite_current_product_binding(value: Any, current_release: str, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: rewrite_current_product_binding(item_value, current_release, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [rewrite_current_product_binding(item, current_release, key) for item in value]
    if isinstance(value, str):
        if key in {"profile", "product_profile", "product_profile_id", "target_product_profile"} and value == LEGACY_PROFILE:
            return CURRENT_PROFILE
        if key in {"bundle_release_id", "release_id", "skill_release"} and LEGACY_RELEASE_RE.fullmatch(value):
            return current_release
    return value


def active_legacy_contamination(target: Path, current_release: str) -> list[str]:
    findings: list[str] = []
    for path in target.rglob("*"):
        relative = path.relative_to(target)
        if path.is_dir():
            if LEGACY_PROFILE in path.name or path.name == "legacy-release-artifacts":
                findings.append(str(relative))
            continue
        if relative in LEGACY_AUDIT_ALLOWLIST or path.suffix.lower() not in {".json", ".md", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if LEGACY_PROFILE in text:
            findings.append(f"{relative}: legacy product profile")
        old_releases = sorted({token for token in LEGACY_RELEASE_RE.findall(text) if token != current_release})
        if old_releases:
            findings.append(f"{relative}: legacy releases {','.join(old_releases)}")
    return sorted(set(findings))


def migrate(source: Path, output: Path | None = None) -> Path:
    source = source.expanduser().resolve()
    if not (source / "project.json").is_file():
        raise FileNotFoundError(f"Not a Jimeng project: {source}")
    source_project = load_json(source / "project.json")
    source_profile = source_project.get("product_profile")
    if source_profile not in {LEGACY_PROFILE, CURRENT_PROFILE}:
        raise ValueError("Migration accepts only legacy durian-daifuku-v1 or durian-daifuku-v2 projects")
    release = load_json(RELEASE_MANIFEST_PATH)
    source_release = ((source_project.get("skill_release_lock") or {}).get("bundle_release_id"))
    if source_profile == CURRENT_PROFILE and source_release == release.get("bundle_release_id"):
        raise ValueError("Project already uses the current release; migration is not a retry mechanism")

    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = output.expanduser().resolve() if output else source.with_name(f"{source.name}-durian-v2-{suffix}")
    if target.exists():
        raise FileExistsError(f"Migration target already exists: {target}")
    shutil.copytree(source, target)

    profile = load_json(PROFILES_DIR / f"{CURRENT_PROFILE}.json")
    write_json(target / "library" / "product_bible.json", profile)

    knowledge_path = target / "library" / "knowledge_index.json"
    knowledge = load_json(knowledge_path)
    knowledge["entries"] = [
        entry
        for entry in knowledge.get("entries") or []
        if not isinstance(entry, dict)
        or (entry.get("applies_to") or {}).get("product_profile") not in {LEGACY_PROFILE, CURRENT_PROFILE}
    ]
    write_json(knowledge_path, knowledge)
    reference_root = target / "source" / "references" / CURRENT_PROFILE
    if reference_root.exists():
        shutil.rmtree(reference_root)
    seeded_assets = seed_product_knowledge_and_assets(target, profile)

    product_library_path = target / "library" / "product_library.json"
    product_library = load_json(product_library_path)
    product_library["products"] = [
        {
            "id": CURRENT_PROFILE,
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
        state["profile"] = CURRENT_PROFILE
        if source_profile == LEGACY_PROFILE:
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
        if source_profile == LEGACY_PROFILE:
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
    project["product_profile"] = CURRENT_PROFILE
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
        "product_profile": CURRENT_PROFILE,
    }
    write_json(workflow_path, workflow)

    stale_paths = (
        Path("prompts"),
        Path("review/prompt_delivery_receipt.json"),
        Path("review/image-generation-requests"),
        Path("review/scale-guides"),
        Path("review/candidates"),
        Path("review/approved"),
        Path("review/diagnostic-failures"),
        Path("review/gallery_receipt.json"),
        Path("review/first_frame_batch_qa.json"),
        Path("candidates"),
        Path("diagnostic-failures"),
        Path("approved"),
        Path("first_frames/candidates"),
        Path("first_frames/prompts"),
        Path("first_frames/approved"),
        Path("first_frames/diagnostic-failures"),
        Path("assets/candidates"),
        Path("assets/approved"),
        Path("assets/diagnostic-failures"),
        Path("planning/asset_reuse_plan.json"),
        Path("planning/product_continuity_lock.json"),
        Path("library/product_bible_override_pending.json"),
        Path("legacy-release-artifacts"),
    )
    removed_paths: list[str] = []
    for relative in stale_paths:
        current = target / relative
        if current.exists():
            remove_path(current)
            removed_paths.append(str(relative))
    for pattern in ("candidate_hard_audit*.json", "*generation*approval*.json", "*generation*receipt*.json"):
        for current in (target / "review").glob(pattern):
            relative = current.relative_to(target)
            remove_path(current)
            removed_paths.append(str(relative))
    reference_parent = target / "source" / "references"
    if reference_parent.is_dir():
        for current in reference_parent.glob(f"*{LEGACY_PROFILE}*"):
            relative = current.relative_to(target)
            remove_path(current)
            removed_paths.append(str(relative))

    intake_contract_path = target / "planning" / "source_intake_contract.json"
    if intake_contract_path.is_file():
        intake_contract = rewrite_current_product_binding(load_json(intake_contract_path), release["bundle_release_id"])
        write_json(intake_contract_path, intake_contract)
    (target / "prompts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(TEMPLATE_DIR / "planning" / "asset_reuse_plan.json", target / "planning" / "asset_reuse_plan.json")
    fresh_reuse_plan = load_json(target / "planning" / "asset_reuse_plan.json")
    fresh_reuse_plan["contract_binding"] = {
        "bundle_release_id": release["bundle_release_id"],
        "prompt_authoring_contract": release["prompt_authoring_contract"],
        "product_profile": CURRENT_PROFILE,
    }
    write_json(target / "planning" / "asset_reuse_plan.json", fresh_reuse_plan)
    write_json(
        target / "review" / "migration_cleanup_receipt.json",
        {
            "schema_version": "video-remix-migration-cleanup-v1.1",
            "status": "legacy_execution_artifacts_removed_from_active_copy",
            "source_project": str(source),
            "source_release": source_release,
            "target_release": release["bundle_release_id"],
            "rollback_source_preserved": True,
            "removed_paths": sorted(set(removed_paths)),
            "active_prompts_cleared": True,
            "active_generation_receipts_cleared": True,
            "active_approved_frames_unbound": True,
            "active_asset_reuse_plan_reset": True,
            "active_recursive_legacy_scan": "pending",
        },
    )
    contamination = active_legacy_contamination(target, release["bundle_release_id"])
    if contamination:
        shutil.rmtree(target)
        raise ValueError("MIGRATED_ACTIVE_COPY_LEGACY_CONTAMINATION: " + "; ".join(contamination))
    cleanup_receipt = load_json(target / "review" / "migration_cleanup_receipt.json")
    cleanup_receipt["active_recursive_legacy_scan"] = "passed"
    write_json(target / "review" / "migration_cleanup_receipt.json", cleanup_receipt)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a non-destructive durian-daifuku-v2 project copy.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = migrate(args.project_dir, args.output)
    requirements = load_json(target / "project.json").get("migration_requirements") or {}
    status = "migrated_requires_manual_rebuild" if requirements.get("requires_manual_shot_map_rebuild") else "migrated_requires_generation_reauthorization"
    print(json.dumps({"status": status, "project_dir": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
