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
CURRENT_EXECUTION_BASIS_RE = re.compile(r"(依据\s+)video-remix-\d+\.\d+\.\d+")
LEGACY_AUDIT_ALLOWLIST = {
    Path("project.json"),
    Path("library/product_bible.json"),
    Path("review/migration_cleanup_receipt.json"),
}


def default_shape_lock(profile: dict[str, Any]) -> dict[str, Any]:
    contract = profile.get("shape_contract") if isinstance(profile.get("shape_contract"), dict) else {}
    return {
        "geometry_identity_id": contract.get("geometry_identity_id"),
        "silhouette_family": contract.get("silhouette_family"),
        "cross_context_identity_required": True,
        "container_shape_inheritance": False,
        "maximum_straight_edge_fraction": contract.get("maximum_straight_edge_fraction"),
        "maximum_right_angle_corner_count": contract.get("maximum_right_angle_corner_count"),
    }


def default_package_content_lock(profile: dict[str, Any]) -> dict[str, Any]:
    contract = profile.get("shape_contract") if isinstance(profile.get("shape_contract"), dict) else {}
    capacity = int(contract.get("package_capacity_count") or 4)
    return {
        "geometry_identity_id": contract.get("geometry_identity_id"),
        "container_geometry_independent": True,
        "tray_cell_role": "support_and_occlusion_only",
        "per_visible_instance_shape_qa": True,
        "package_capacity_count": capacity,
        "accounted_product_count": capacity,
        "instance_ids": [f"DF-PKG-{index:02d}" for index in range(1, capacity + 1)],
    }


def default_integrity_lock(state_id: str) -> dict[str, Any]:
    whole_states = {"whole", "held", "plated", "pressed", "global_stretch"}
    opening_origin = {
        "opening_window_seed": "two_hand_tension",
        "opening_window_established": "two_hand_tension",
        "pre_break": "two_hand_tension",
        "break": "two_hand_tension",
        "hand_torn_cross_section": "hand_torn",
        "early_cohesive_opening": "hand_torn",
        "two_halves_display": "hand_torn",
        "bitten": "migration_required",
    }.get(state_id, "none" if state_id in whole_states else "migration_required")
    value = {
        "declared_state": state_id,
        "opening_origin": opening_origin,
        "filling_visibility": "none" if state_id in whole_states else ("bite_notch_only" if state_id == "bitten" else "state_bounded_opening"),
        "whole_shell_closed": state_id in whole_states,
        "large_excavated_crater": False,
        "peeled_top_cap": False,
        "scooped_hollow": False,
        "open_basin": False,
        "hand_torn_hole_as_bite": False,
    }
    if state_id == "bitten":
        value.update(
            {
                "opening_direction": "migration_required",
                "mouth_contacts_opening_side": False,
                "camera_facing_opening": None,
                "orientation_evidence": "",
            }
        )
    return value


def default_instance_lock(state: dict[str, Any]) -> dict[str, Any]:
    try:
        count = max(1, int(state.get("count") or 1))
    except (TypeError, ValueError):
        count = 1
    return {
        "source_product_count": count,
        "target_product_count": count,
        "count_change_event": "none",
        "instance_ids": [f"DF-{index:02d}" for index in range(1, count + 1)],
        "shape_variant_ids": [f"DF-VAR-{index:02d}" for index in range(1, count + 1)],
        "shared_size_class": "DF2-7CM-MAX7.5CM",
        "pixel_identical_clones": False,
        "contact_deformation": "slight_local_flattening_with_volume_conservation" if state.get("state") == "held" and count > 1 else "natural_support_only",
    }


def default_arrangement_lock(shot_id: Any, state: dict[str, Any]) -> dict[str, Any]:
    instance_ids = list((state.get("instance_lock") or {}).get("instance_ids") or [])
    packaging_visible = state.get("packaging") not in (None, False, "none", "hidden")
    return {
        "layout_id": f"LAYOUT-{shot_id}",
        "previous_layout_id": None,
        "container_id": "PACKAGE-REQUIRES-REBIND" if packaging_visible else "CONTAINER-REQUIRES-REBIND",
        "inventory_stage_id": f"INV-{shot_id}",
        "instance_ids": instance_ids,
        "natural_irregularity_required": packaging_visible,
        "perfect_grid": False,
        "equal_spacing": False,
        "uniform_orientation": False,
        "relative_topology_preserved": True,
        "event": {"type": "initial"},
        "migration_status": "manual_cross_shot_rebind_required",
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


def rebind_current_execution_basis(path: Path, current_release: str) -> bool:
    """Rebind only an explicit '依据 <release>' execution directive.

    Other old-release mentions remain untouched so the recursive contamination
    scan can block ambiguous history, prohibitions or rollback instructions.
    """
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    updated = CURRENT_EXECUTION_BASIS_RE.sub(lambda match: f"{match.group(1)}{current_release}", original)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


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
    topology_rebind_shot_ids: list[str] = []
    layout_rebind_shot_ids: list[str] = []
    for shot in shots.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        state = shot.get("product_state") or {}
        legacy_state = state.get("state")
        state["profile"] = CURRENT_PROFILE
        if source_profile == LEGACY_PROFILE:
            state["legacy_state"] = legacy_state
            for field in ("scale_lock", "shape_lock", "integrity_lock", "instance_lock", "arrangement_lock", "package_content_lock", "surface_lock", "filling_lock", "endpoint_lock", "reference_roles"):
                state.pop(field, None)
            if legacy_state in AMBIGUOUS_STATES or legacy_state not in UNAMBIGUOUS_STATES:
                state["state"] = "migration_required"
                ambiguous.append({"shot_id": shot.get("id"), "legacy_state": legacy_state})
        else:
            scale_lock = state.get("scale_lock") if isinstance(state.get("scale_lock"), dict) else {}
            scale_lock.pop("pixel_plan", None)
            if scale_lock:
                state["scale_lock"] = scale_lock
            if legacy_state == "migration_required":
                ambiguous.append({"shot_id": shot.get("id"), "legacy_state": state.get("legacy_state")})
        state["shape_lock"] = default_shape_lock(profile)
        state["integrity_lock"] = default_integrity_lock(str(state.get("state") or "migration_required"))
        if state.get("packaging") not in (None, False, "none", "hidden"):
            state["count"] = int((profile.get("shape_contract") or {}).get("package_capacity_count") or 4)
        state["instance_lock"] = default_instance_lock(state)
        if state.get("state") == "bitten":
            topology_rebind_shot_ids.append(str(shot.get("id")))
        if state.get("packaging") not in (None, False, "none", "hidden"):
            state["package_content_lock"] = default_package_content_lock(profile)
        else:
            state.pop("package_content_lock", None)
        try:
            count = int(state.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        if state.get("packaging") not in (None, False, "none", "hidden") or state.get("state") == "plated" or count > 1:
            state["arrangement_lock"] = default_arrangement_lock(shot.get("id"), state)
            layout_rebind_shot_ids.append(str(shot.get("id")))
        else:
            state.pop("arrangement_lock", None)
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
        "requires_manual_shot_map_rebuild": bool(ambiguous or topology_rebind_shot_ids or layout_rebind_shot_ids),
        "requires_generation_reauthorization": True,
        "reason": "Rebind ambiguous v1 states when present, rebuild every pixel plan, and authorize all new image requests from the exact original frame under the current release.",
        "ambiguous_legacy_states": ambiguous,
        "topology_rebind_shot_ids": sorted(set(topology_rebind_shot_ids)),
        "layout_rebind_shot_ids": sorted(set(layout_rebind_shot_ids)),
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
    rebound_directive_paths: list[str] = []
    user_directives_path = target / "planning" / "user_directives.md"
    if rebind_current_execution_basis(user_directives_path, release["bundle_release_id"]):
        rebound_directive_paths.append(str(user_directives_path.relative_to(target)))
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
            "current_execution_basis_rebound_paths": rebound_directive_paths,
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
