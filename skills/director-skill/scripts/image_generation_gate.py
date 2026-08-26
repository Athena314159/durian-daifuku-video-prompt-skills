#!/usr/bin/env python3
"""Authorize and record image generation without allowing legacy or half-edit bypasses."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from correction_memory import normalize_memory


SKILL_DIR = Path(__file__).resolve().parent.parent
RELEASE_PATH = SKILL_DIR / "references" / "skill-release.json"
IMAGE_EXECUTION_TIERS = {"first_frame_only", "full_delivery"}
GENERATION_PROMPT_MARKER = "GENERATION_HARD_RULES_V1"
GENERATION_PROMPT_HEADERS = ("【生成目标与叙事职责】", "【产品与动作物理】", "【生图硬性规则】")
JOINT_QA_KEYS = ("identity", "product", "scale", "shape", "state_topology", "instance_count", "instance_variation", "layout_continuity", "inventory_transition", "package_product_geometry", "surface", "filling", "endpoint", "composition", "source_provenance")
HAND_INTERACTION_STATES = {
    "held", "pressed", "global_stretch", "opening_window_seed", "opening_window_established",
    "pre_break", "break", "hand_torn_cross_section", "early_cohesive_opening", "two_halves_display", "bitten",
}


class GateError(ValueError):
    """A fail-closed image-generation contract violation."""


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise GateError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def prompt_text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def applicable_correction_rules(project: dict[str, Any], shot: dict[str, Any], project_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """Read the current correction memory that must be compiled into this request."""
    path = project_dir / "library" / "correction_memory.json"
    if not path.is_file():
        raise GateError("CORRECTION_MEMORY_MISSING: persist user feedback before image authorization")
    memory = read_json(path)
    memory, _ = normalize_memory(
        memory,
        project_id=str(project.get("project_id") or ""),
        product_profile=str(project.get("product_profile") or ""),
        style_profile=str(project.get("style_profile") or ""),
    )
    rules = memory.get("rules")
    if rules is None:
        rules = []
    if not isinstance(rules, list):
        raise GateError("CORRECTION_MEMORY_INVALID: rules must be a list")
    expected = {
        "shot": shot.get("id"),
        "project": project.get("project_id"),
        "product": project.get("product_profile"),
        "style": project.get("style_profile"),
    }
    selected: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("active") is not True:
            continue
        scope, target = rule.get("scope"), rule.get("target")
        if scope not in expected:
            continue
        if target in (None, "*", expected[scope]):
            selected.append({
                "id": rule.get("id"),
                "scope": scope,
                "target": target,
                "priority": rule.get("priority"),
                "instruction": rule.get("instruction"),
            })
    selected.sort(key=lambda item: (int(item.get("priority") or 0), str(item.get("id") or "")), reverse=True)
    return sha256_file(path), selected


def validate_generation_prompt_text(
    prompt_text: str,
    project: dict[str, Any],
    product: dict[str, Any],
    shot: dict[str, Any],
    exact_contract: dict[str, Any],
    package_bindings: list[dict[str, Any]],
    correction_rules: list[dict[str, Any]],
    face_paths: list[Path],
) -> list[str]:
    """Fail authorization when review-only rules are absent from submitted text."""
    normalized = prompt_text.replace(" ", "").replace("\n", "")
    missing: list[str] = [header for header in GENERATION_PROMPT_HEADERS if header not in prompt_text]
    if GENERATION_PROMPT_MARKER not in prompt_text:
        missing.append(GENERATION_PROMPT_MARKER)
    if "无字幕" not in normalized and "无新增字幕" not in normalized:
        missing.append("no_subtitles")
    if "无水印" not in normalized and "无新增水印" not in normalized:
        missing.append("no_watermark")
    if not any(term in normalized for term in ("原始首帧", "精确原图", "同一原图", "原图")):
        missing.append("exact_original_source")
    visible = exact_contract.get("product_visibility") != "absent"
    if visible:
        product_name = str(product.get("name") or "").strip()
        product_profile = str(product.get("profile_id") or project.get("product_profile") or "").strip()
        if not any(term and term in prompt_text for term in (product_name, product_profile)):
            missing.append("target_product_identity")
        if project.get("product_mode") == "replace_product" and not any(
            term in normalized for term in ("原视频旧食品", "原产品", "旧产品")
        ):
            missing.append("source_product_removal")
        if product.get("profile_id") == "durian-daifuku-v2":
            for token in ("7厘米", "暖奶白", "果泥", "糯米粉雾"):
                if token not in prompt_text:
                    missing.append(f"durian_{token}")
    else:
        if not any(term in normalized for term in ("不出现产品", "不提前出现产品", "product_visibility=absent", "产品0")):
            missing.append("exact_frame_product_absence")
    if face_paths and not any(term in normalized for term in ("换脸", "换人物身份", "身份与产品", "授权身份")):
        missing.append("identity_replacement_binding")
    if package_bindings and not any(term in normalized for term in ("批准母版", "包装母版", "批准资产")):
        missing.append("approved_packaging_master")
    for rule in correction_rules:
        instruction = str(rule.get("instruction") or "").strip()
        if instruction and instruction not in prompt_text:
            missing.append(f"correction_rule:{rule.get('id')}")
    return sorted(set(missing))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve(project_dir: Path, value: Any) -> Path:
    path = Path(str(value or "")).expanduser()
    return path.resolve() if path.is_absolute() else (project_dir / path).resolve()


def relative(project_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def find_shot(manifest: dict[str, Any], shot_id: str) -> dict[str, Any]:
    matches = [item for item in manifest.get("shots") or [] if isinstance(item, dict) and item.get("id") == shot_id]
    if len(matches) != 1:
        raise GateError(f"Expected exactly one shot {shot_id!r}; found {len(matches)}")
    return matches[0]


def find_unit(shot: dict[str, Any], unit_id: str | None) -> dict[str, Any] | None:
    if not unit_id:
        return None
    units = [
        item
        for key in ("source_units", "inserted_units")
        for item in (shot.get(key) or [])
        if isinstance(item, dict)
        and unit_id in {str(item.get("source_shot_id") or ""), str(item.get("inserted_shot_id") or "")}
    ]
    if len(units) != 1:
        raise GateError(f"EXPECTED_EXACTLY_ONE_DELIVERY_UNIT: {unit_id!r} matched {len(units)} units in shot {shot.get('id')}")
    return units[0]


def exact_frame_context(project_dir: Path, shot: dict[str, Any], unit: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Resolve a delivery-unit-specific exact-frame contract without mutating video terminal state."""
    contract_owner = unit if unit is not None else (shot.get("asset_links") or {})
    contract = contract_owner.get("exact_first_frame_generation_contract")
    if not isinstance(contract, dict):
        contract = {"product_visibility": "present"}
    visibility = contract.get("product_visibility", "present")
    if visibility not in {"present", "absent"}:
        raise GateError("EXACT_FIRST_FRAME_PRODUCT_VISIBILITY_INVALID: expected present or absent")

    effective = copy.deepcopy(shot)
    effective_assets = effective.setdefault("asset_links", {})
    unit_source = None
    if unit is not None:
        unit_source = unit.get("source_first_frame") or unit.get("source_reference_frame")
    source_value = contract.get("source_frame") or unit_source or effective_assets.get("source_first_frame")
    source = resolve(project_dir, source_value)
    effective_assets["source_first_frame"] = relative(project_dir, source)

    if isinstance(contract.get("product_state"), dict):
        effective["product_state"] = copy.deepcopy(contract["product_state"])
    if isinstance(contract.get("product_references"), list):
        effective_assets["product_references"] = copy.deepcopy(contract["product_references"])

    evidence = contract.get("source_observation") if isinstance(contract.get("source_observation"), dict) else {}
    if visibility == "absent":
        source_visible = evidence.get("product_visible")
        source_count = evidence.get("visible_product_count")
        if source_visible not in {True, False} or not isinstance(source_count, int) or source_count < 0 or (source_visible is False and source_count != 0) or (source_visible is True and source_count < 1):
            raise GateError("EXACT_FIRST_FRAME_SOURCE_PRODUCT_EVIDENCE_INVALID: source visibility and count must describe the original frame truthfully")
        if contract.get("product_reference_inputs_required") is not False or contract.get("visible_target_product_count") != 0:
            raise GateError("EXACT_FIRST_FRAME_PRODUCT_ABSENT_MISMATCH: target-absent frames require zero target products and no target product references")
        if contract.get("product_edit_required") is not source_visible:
            raise GateError("EXACT_FIRST_FRAME_PRODUCT_EDIT_MODE_MISMATCH: removing a visible source product requires a product edit; source-absent frames require none")
        packaging = unit.get("packaging_evidence") if isinstance(unit, dict) and isinstance(unit.get("packaging_evidence"), dict) else {}
        if packaging.get("visible") is True:
            if source_visible is not True or contract.get("source_product_action") != "neutralize_to_non_product_carrier":
                raise GateError("EXACT_FIRST_FRAME_SOURCE_PACKAGE_ACTION_MISSING: visible source packaging must be explicitly neutralized to the approved non-product carrier")
        if contract.get("pixel_plan_applicability") != "not_applicable_product_absent":
            raise GateError("EXACT_FIRST_FRAME_PRODUCT_ABSENT_MISMATCH: absent exact frames must declare pixel_plan_applicability=not_applicable_product_absent")
    else:
        if contract.get("product_edit_required") is False or contract.get("product_reference_inputs_required") is False:
            raise GateError("NON_CANONICAL_PRODUCT_ABSENCE_BYPASS: product-present exact frames cannot disable replacement inputs")
    return contract, effective, source


def requires_hand_scale_relationship(shot: dict[str, Any]) -> bool:
    state = shot.get("product_state") if isinstance(shot.get("product_state"), dict) else {}
    character = shot.get("character") if isinstance(shot.get("character"), dict) else {}
    return (
        state.get("state") in HAND_INTERACTION_STATES
        or shot.get("visual_type") == "person_eating"
        or character.get("hands_only") is True
    )


def shot_contract_value(shot: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(shot, ensure_ascii=False))
    assets = value.get("asset_links") if isinstance(value.get("asset_links"), dict) else {}
    for key in (
        "image_generation_authorization",
        "image_generation_result_receipt",
        "candidate_generation_first_frame",
        "approved_generation_first_frame",
    ):
        assets.pop(key, None)
    value["asset_links"] = assets
    for key in ("source_units", "inserted_units"):
        for unit in value.get(key) or []:
            if isinstance(unit, dict):
                for volatile in (
                    "image_generation_authorization",
                    "image_generation_result_receipt",
                    "candidate_generation_first_frame",
                    "approved_generation_first_frame",
                ):
                    unit.pop(volatile, None)
    return value


def unit_contract_value(unit: dict[str, Any] | None) -> dict[str, Any] | None:
    if unit is None:
        return None
    value = copy.deepcopy(unit)
    for volatile in (
        "image_generation_authorization",
        "image_generation_result_receipt",
        "candidate_generation_first_frame",
        "approved_generation_first_frame",
    ):
        value.pop(volatile, None)
    return value


def load_current_release() -> dict[str, Any]:
    return read_json(RELEASE_PATH)


def assert_current_project(
    project: dict[str, Any],
    *,
    project_dir: Path | None = None,
    require_image_tier: bool = True,
    allow_local_repair: bool = False,
) -> dict[str, Any]:
    """Check the immutable release contract before image generation.

    A normal generation request must be locked to the live bundle.  The one
    exception is the explicit ``--local-repair`` lane: it may use an older
    project lock only when the authoring contract is identical and the target
    is not the retired v1 product contract.  This keeps an old project from
    being silently upgraded while still allowing one failed SRC/ADD to be
    repaired without reopening the entire project.
    """
    release = load_current_release()
    if project_dir is not None:
        redirect_path = project_dir / "planning" / "execution_redirect.json"
        if redirect_path.is_file():
            redirect = read_json(redirect_path)
            if redirect.get("status") == "redirected":
                raise GateError(f"PROJECT_EXECUTION_REDIRECTED: continue from {redirect.get('target_project') or 'the recorded migrated target'}")
    lock = project.get("skill_release_lock") if isinstance(project.get("skill_release_lock"), dict) else {}
    current_id = release.get("bundle_release_id")
    locked_id = lock.get("bundle_release_id")
    release_mismatch = locked_id != current_id
    if release_mismatch and not allow_local_repair:
        raise GateError(
            f"LEGACY_PROJECT_GENERATION_BLOCKED: project is locked to {locked_id or 'unmanaged-legacy'}, "
            f"current release is {current_id}; create an explicit non-destructive migration copy first"
        )
    if lock.get("prompt_authoring_contract") != release.get("prompt_authoring_contract") or lock.get("auto_upgrade") is not False:
        raise GateError("EXPLICIT_MIGRATION_REQUIRED: release lock does not match the current immutable authoring contract")
    if release_mismatch and allow_local_repair and not locked_id:
        raise GateError("LOCAL_REPAIR_PROJECT_LOCK_MISSING: bind the project to a prior immutable release before using --local-repair")
    if project.get("product_profile") == "durian-daifuku-v1":
        raise GateError("LEGACY_PRODUCT_CONTRACT_BLOCKED: durian-daifuku-v1 is read-only and must migrate to v2")
    if require_image_tier and project.get("execution_tier") not in IMAGE_EXECUTION_TIERS:
        raise GateError("IMAGE_EXECUTION_TIER_BLOCKED: image calls are only allowed in first_frame_only or full_delivery")
    return release


def avatar_reference_paths(project_dir: Path, shot: dict[str, Any], selected: list[str]) -> list[Path]:
    assets = shot.get("asset_links") if isinstance(shot.get("asset_links"), dict) else {}
    edit_chain = assets.get("edit_chain") if isinstance(assets.get("edit_chain"), dict) else {}
    face_enabled = edit_chain.get("face_edit_enabled") is True
    if (assets.get("avatar_reference") or edit_chain.get("face_reference_ids")) and not face_enabled:
        raise GateError("FACE_EDIT_FLAG_BYPASS: bound avatar references require face_edit_enabled=true")
    if not face_enabled:
        if selected:
            raise GateError("UNAUTHORIZED_IDENTITY_EDIT: face references were supplied but face_edit_enabled is not true")
        return []
    avatar_id = assets.get("avatar_reference")
    allowed_ids = [str(value) for value in edit_chain.get("face_reference_ids") or []]
    if not avatar_id or avatar_id not in allowed_ids:
        raise GateError("FACE_REFERENCE_BINDING_MISSING: bind one authorized avatar in avatar_reference and face_reference_ids")
    library = read_json(project_dir / "library" / "avatar_library.json")
    avatars = [item for item in library.get("avatars") or [] if isinstance(item, dict) and item.get("id") == avatar_id]
    if len(avatars) != 1 or avatars[0].get("portrait_rights_cleared") is not True:
        raise GateError("FACE_REFERENCE_NOT_AUTHORIZED: selected avatar is missing or portrait rights are not cleared")
    raw_assets = avatars[0].get("reference_assets") if isinstance(avatars[0].get("reference_assets"), dict) else {}
    allowed_paths: set[Path] = set()
    for value in raw_assets.values():
        for raw in value if isinstance(value, list) else [value]:
            if isinstance(raw, str) and raw.strip():
                allowed_paths.add(resolve(project_dir, raw))
    chosen = [resolve(project_dir, value) for value in selected]
    if not chosen:
        raise GateError("FACE_REFERENCE_FILES_MISSING: select at least one authorized identity-only reference file")
    if any(path not in allowed_paths for path in chosen):
        raise GateError("FACE_REFERENCE_OUTSIDE_AUTHORIZED_AVATAR: selected file is not registered under the shot avatar")
    return chosen


def product_reference_paths(project_dir: Path, project: dict[str, Any], shot: dict[str, Any]) -> list[Path]:
    assets = shot.get("asset_links") if isinstance(shot.get("asset_links"), dict) else {}
    values = [value for value in assets.get("product_references") or [] if isinstance(value, str) and value.strip()]
    if project.get("product_mode") == "replace_product" and not values:
        raise GateError("PRODUCT_REFERENCE_FILES_MISSING: replacement shots require approved product references")
    return [resolve(project_dir, value) for value in values]


def packaging_reference_binding(project_dir: Path, project: dict[str, Any], product: dict[str, Any], shot: dict[str, Any]) -> list[dict[str, Any]]:
    if project.get("product_profile") != "durian-daifuku-v2":
        return []
    state = shot.get("product_state") if isinstance(shot.get("product_state"), dict) else {}
    lock = state.get("packaging_lock") if isinstance(state.get("packaging_lock"), dict) else None
    visible = lock.get("visible") is True if lock is not None else state.get("packaging") not in (None, False, "none", "hidden")
    inventory = state.get("source_package_inventory") if isinstance(state.get("source_package_inventory"), dict) else None
    if inventory is None:
        raise GateError("DAIFUKU_SOURCE_PACKAGE_INVENTORY_MISSING: inspect the exact source frame before deciding package visibility")
    source_carton_present = inventory.get("shipping_carton_present") is True
    if not visible:
        if source_carton_present and inventory.get("visibility_transition") not in {"removed_before_exact_frame", "fully_occluded_with_evidence"}:
            raise GateError("DAIFUKU_SOURCE_PACKAGE_VISIBILITY_MISMATCH: source carton cannot be silently omitted")
        return []
    if not lock:
        raise GateError("DAIFUKU_PACKAGING_LOCK_MISSING: packaging-visible shots require exact level, face and asset bindings")
    if lock.get("invented_packaging_allowed") is not False or lock.get("artwork_mode") != "approved_master_projection_only":
        raise GateError("DAIFUKU_PACKAGING_INVENTION_FORBIDDEN: only projection from an approved packaging master is allowed")
    contract = product.get("packaging_contract") if isinstance(product.get("packaging_contract"), dict) else {}
    levels = lock.get("package_levels") if isinstance(lock.get("package_levels"), list) else []
    if not levels or len(set(map(str, levels))) != len(levels):
        raise GateError("DAIFUKU_PACKAGING_LEVEL_INVALID: declare each visible package level exactly once")
    allowed_levels = set(contract.get("package_levels") or [])
    faces_by_level = lock.get("visible_faces_by_level") if isinstance(lock.get("visible_faces_by_level"), dict) else {}
    ids_by_level = lock.get("reference_asset_ids_by_level") if isinstance(lock.get("reference_asset_ids_by_level"), dict) else {}
    linked = {str(value) for value in ((shot.get("asset_links") or {}).get("product_references") or []) if isinstance(value, str) and value.strip()}
    approved = {
        str(item.get("id")): item
        for item in product.get("reference_assets") or []
        if isinstance(item, dict) and item.get("approved") is True and item.get("id")
    }
    records: list[dict[str, str]] = []
    target_carton_present = "shipping_carton" in levels
    if target_carton_present != source_carton_present and inventory.get("user_authorized_level_change") is not True:
        raise GateError("DAIFUKU_SOURCE_PACKAGE_INVENTORY_MISMATCH: target package levels must preserve the exact source frame unless a user-authorized level change is recorded")
    for level in levels:
        if level not in allowed_levels:
            raise GateError(f"DAIFUKU_PACKAGING_LEVEL_INVALID: {level!r} is not an approved packaging layer")
        faces = faces_by_level.get(level) if isinstance(faces_by_level.get(level), list) else []
        assets_for_level = ids_by_level.get(level) if isinstance(ids_by_level.get(level), dict) else {}
        if not faces:
            raise GateError(f"DAIFUKU_PACKAGING_FACE_MISSING: {level} has no declared visible face")
        for face in faces:
            asset_id = assets_for_level.get(face)
            asset = approved.get(str(asset_id))
            if not asset:
                raise GateError(f"DAIFUKU_PACKAGING_REFERENCE_MISSING: no approved asset for {level}/{face}")
            if asset.get("packaging_level") != level or asset.get("visible_face") != face:
                raise GateError(f"DAIFUKU_PACKAGING_LAYER_MISMATCH: {asset_id} cannot satisfy {level}/{face}")
            if asset.get("user_approved") is not True:
                raise GateError(f"DAIFUKU_PACKAGING_REFERENCE_NOT_USER_APPROVED: {asset_id}")
            expected = (((contract.get("required_reference_assets_by_level_and_face") or {}).get(level) or {}).get(face))
            if asset_id != expected:
                raise GateError(f"DAIFUKU_PACKAGING_REFERENCE_MISSING: {level}/{face} must bind {expected!r}")
            if level == "shipping_carton":
                region = asset.get("source_region") if isinstance(asset.get("source_region"), dict) else {}
                if asset.get("master_mode") != "perspective_composite_with_rectified_region":
                    raise GateError("DAIFUKU_CARTON_COMPOSITE_MODE_INVALID: carton faces must come from explicit rectified regions")
                if asset.get("projection_required") is True:
                    quad = region.get("quad_xy")
                    if not isinstance(quad, list) or len(quad) != 4 or any(not isinstance(point, list) or len(point) != 2 for point in quad):
                        raise GateError("DAIFUKU_CARTON_FACE_REGION_MISSING: visible printed carton faces require a distinct source quadrilateral")
                elif region.get("geometry_reference_only") is not True:
                    raise GateError("DAIFUKU_CARTON_FACE_ROLE_INVALID: non-projected carton region must be geometry-reference-only")
            path_value = str(asset.get("target_path") or asset.get("path") or "")
            if path_value not in linked:
                raise GateError(f"DAIFUKU_PACKAGING_REFERENCE_MISSING: link exact file {path_value!r}; body-only references cannot authorize packaging")
            path = resolve(project_dir, path_value)
            if not path.is_file() or (asset.get("sha256") and asset.get("sha256") != sha256_file(path)):
                raise GateError(f"DAIFUKU_PACKAGING_REFERENCE_HASH_MISMATCH: {path}")
            records.append({"contract_type": "approved_package_face", "packaging_level": str(level), "visible_face": str(face), "asset_id": str(asset_id), "path": relative(project_dir, path), "sha256": sha256_file(path), "source_region": asset.get("source_region"), "projection_required": asset.get("projection_required")})
    if target_carton_present:
        plan_value = lock.get("shipping_carton_capacity_plan")
        plan_path = resolve(project_dir, plan_value)
        if not plan_value or not plan_path.is_file():
            raise GateError("SHIPPING_CARTON_CAPACITY_PLAN_MISSING: infer carton dimensions and capacity before generation")
        plan = read_json(plan_path)
        capacity = plan.get("capacity_check") if isinstance(plan.get("capacity_check"), dict) else {}
        if plan.get("schema_version") != "shipping-carton-capacity-plan-v1.0" or plan.get("status") != "authorized" or plan.get("generation_authorized") is not True or capacity.get("pass") is not True:
            code = plan.get("error_code") or "SOURCE_CONTAINER_CAPACITY_CONFLICT"
            raise GateError(f"{code}: unresolved shipping-carton capacity plan; stop generation")
        if capacity.get("retail_box_dimensions_cm") != [15.0, 15.0, 4.5] or plan.get("product_scale_may_shrink_to_fit") is not False:
            raise GateError("SHIPPING_CARTON_CONTENT_SCALE_INVALID: carton inference cannot shrink retail boxes or daifuku")
        records.append({"contract_type": "shipping_carton_capacity_plan", "packaging_level": "shipping_carton", "visible_face": "capacity", "asset_id": "SHIPPING-CARTON-CAPACITY-PLAN", "path": relative(project_dir, plan_path), "sha256": sha256_file(plan_path)})
    return records


def hash_records(project_dir: Path, paths: list[Path], role: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            raise GateError(f"BOUND_REFERENCE_UNAVAILABLE: {path}")
        records.append({"path": relative(project_dir, path), "sha256": sha256_file(path), "role": role})
    return records


def validate_pixel_plan(
    project_dir: Path,
    project: dict[str, Any],
    product: dict[str, Any],
    shot: dict[str, Any],
    *,
    product_visible: bool = True,
    allowed_release_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    if project.get("product_profile") != "durian-daifuku-v2":
        return None
    if not product_visible:
        return None
    state = shot.get("product_state") if isinstance(shot.get("product_state"), dict) else {}
    scale_lock = state.get("scale_lock") if isinstance(state.get("scale_lock"), dict) else {}
    plan = scale_lock.get("pixel_plan") if isinstance(scale_lock.get("pixel_plan"), dict) else None
    if not plan or plan.get("status") != "authorized":
        raise GateError("DAIFUKU_PIXEL_PREFLIGHT_MISSING: authorize a bound pixel plan before image generation")
    source = resolve(project_dir, (shot.get("asset_links") or {}).get("source_first_frame"))
    guide = resolve(project_dir, plan.get("guide_path"))
    manifest = resolve(project_dir, plan.get("manifest_path"))
    if resolve(project_dir, plan.get("source_frame")) != source or plan.get("source_frame_sha256") != sha256_file(source):
        raise GateError("DAIFUKU_PIXEL_SOURCE_MISMATCH: pixel plan does not bind the exact original source frame")
    if not guide.is_file() or plan.get("guide_sha256") != sha256_file(guide):
        raise GateError("DAIFUKU_SCALE_GUIDE_HASH_MISMATCH: rebuild the deterministic guide")
    if not manifest.is_file():
        raise GateError("DAIFUKU_SCALE_GUIDE_MANIFEST_MISSING")
    binding = plan.get("contract_binding") if isinstance(plan.get("contract_binding"), dict) else {}
    anchor = scale_lock.get("anchor") if isinstance(scale_lock.get("anchor"), dict) else {}
    expected = {
        "bundle_release_id": load_current_release().get("bundle_release_id"),
        "product_profile": project.get("product_profile"),
        "product_version": product.get("version"),
        "product_bible_sha256": sha256_file(project_dir / "library" / "product_bible.json"),
        "state": state.get("state"),
        "anchor_type": anchor.get("type"),
        "anchor_expected_ratio": anchor.get("expected_ratio"),
    }
    release_binding = binding.get("bundle_release_id")
    release_ok = release_binding == expected["bundle_release_id"] or (
        allowed_release_ids is not None and release_binding in allowed_release_ids
    )
    if not release_ok or any(binding.get(key) != value for key, value in expected.items() if key != "bundle_release_id"):
        raise GateError("DAIFUKU_PIXEL_CONTRACT_STALE: release, product, state or anchor changed after pixel preflight")
    pixel_anchor = plan.get("anchor") if isinstance(plan.get("anchor"), dict) else {}
    box = pixel_anchor.get("measurement_bbox_xywh")
    if pixel_anchor.get("measurement_method") != "annotated_bbox" or not isinstance(box, list) or len(box) != 4:
        raise GateError("DAIFUKU_ANCHOR_MEASUREMENT_UNPROVEN: use an annotated in-frame anchor bbox")
    if pixel_anchor.get("type") != anchor.get("type") or pixel_anchor.get("expected_ratio") != anchor.get("expected_ratio"):
        raise GateError("DAIFUKU_PIXEL_ANCHOR_MISMATCH: plan anchor must equal the shot scale lock")
    if requires_hand_scale_relationship(shot) and pixel_anchor.get("type") == "approved_scene_scale_master":
        raise GateError("DAIFUKU_SCENE_ONLY_SCALE_ANCHOR_FORBIDDEN: hand-interaction scale must originate from a physical same-depth anchor")
    return plan


def authorize(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.expanduser().resolve()
    project = read_json(project_dir / "project.json")
    local_repair = bool(getattr(args, "local_repair", False))
    release = assert_current_project(project, project_dir=project_dir, allow_local_repair=local_repair)
    product = read_json(project_dir / "library" / "product_bible.json")
    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = read_json(manifest_path)
    shot = find_shot(manifest, args.shot_id)
    delivery_units = [
        item
        for key in ("source_units", "inserted_units")
        for item in (shot.get(key) or [])
        if isinstance(item, dict)
    ]
    if len(delivery_units) > 1 and not getattr(args, "unit_id", None):
        raise GateError("UNIT_SCOPED_IMAGE_AUTHORIZATION_REQUIRED: multi-unit shots must authorize each SRC/ADD independently with --unit-id")
    unit = find_unit(shot, getattr(args, "unit_id", None))
    exact_contract, effective_shot, source = exact_frame_context(project_dir, shot, unit)
    prompt = args.prompt_file.expanduser().resolve()
    if not source.is_file():
        raise GateError("ORIGINAL_SOURCE_FRAME_MISSING: retries cannot fall back to a generated candidate")
    prompt_text = prompt.read_text(encoding="utf-8") if prompt.is_file() else ""
    if not prompt.is_file() or not prompt_text.strip():
        raise GateError("IMAGE_PROMPT_MISSING: persist the exact submitted prompt before authorization")

    face_paths = avatar_reference_paths(project_dir, effective_shot, args.face_reference or [])
    product_visible = exact_contract.get("product_visibility") != "absent"
    product_paths = product_reference_paths(project_dir, project, effective_shot) if product_visible else []
    package_bindings = packaging_reference_binding(project_dir, project, product, effective_shot) if product_visible else []
    correction_memory_sha256, correction_rules = applicable_correction_rules(project, effective_shot, project_dir)
    prompt_errors = validate_generation_prompt_text(
        prompt_text,
        project,
        product,
        effective_shot,
        exact_contract,
        package_bindings,
        correction_rules,
        face_paths,
    )
    if prompt_errors:
        raise GateError(
            "GENERATION_PROMPT_RULES_MISSING: the exact text submitted to image generation is missing "
            + ", ".join(prompt_errors)
        )
    identity_required = bool(face_paths)
    product_required = project.get("product_mode") == "replace_product" and (
        product_visible or exact_contract.get("product_edit_required") is True
    )
    requested = sorted(set(args.edit or []))
    expected_edits = sorted([name for name, needed in (("identity", identity_required), ("product", product_required)) if needed])
    if requested != expected_edits:
        raise GateError(f"ATOMIC_EDIT_SET_MISMATCH: requested edits must be exactly {expected_edits}, got {requested}")
    atomic = identity_required and product_required
    if atomic and args.atomic is not True:
        raise GateError("ATOMIC_IDENTITY_PRODUCT_REQUIRED: identity and product replacement must run in one request")
    if not atomic and args.atomic is True:
        raise GateError("ATOMIC_FLAG_INVALID: atomic is reserved for shots that require both identity and product")

    lock = project.get("skill_release_lock") if isinstance(project.get("skill_release_lock"), dict) else {}
    allowed_pixel_releases = {str(lock.get("bundle_release_id"))} if local_repair and lock.get("bundle_release_id") else None
    pixel_plan = validate_pixel_plan(
        project_dir,
        project,
        product,
        effective_shot,
        product_visible=product_visible,
        allowed_release_ids=allowed_pixel_releases,
    )
    source_record = hash_records(project_dir, [source], "immutable_original_source")[0]
    face_records = hash_records(project_dir, face_paths, "identity_only")
    product_records = hash_records(project_dir, product_paths, "product_role_bound")
    guide_records: list[dict[str, str]] = []
    if pixel_plan:
        guide_records = hash_records(project_dir, [resolve(project_dir, pixel_plan.get("guide_path"))], "geometry_only_do_not_render_overlay")
    payload = {
        "schema_version": "image-generation-authorization-v1.0",
        "status": "authorized",
        "bundle_release_id": release.get("bundle_release_id"),
        "release_compatibility_lane": "explicit_local_shot_repair" if local_repair and lock.get("bundle_release_id") != release.get("bundle_release_id") else None,
        "project_locked_bundle_release_id": lock.get("bundle_release_id"),
        "prompt_authoring_contract": release.get("prompt_authoring_contract"),
        "project_id": project.get("project_id"),
        "shot_id": args.shot_id,
        "unit_id": getattr(args, "unit_id", None),
        "exact_first_frame_generation_contract": exact_contract,
        "product_profile": project.get("product_profile"),
        "product_version": product.get("version"),
        "product_bible_sha256": sha256_file(project_dir / "library" / "product_bible.json"),
        "shot_contract_sha256": canonical_hash(shot_contract_value(shot)),
        "unit_contract_sha256": canonical_hash(unit_contract_value(unit)) if unit is not None else None,
        "requested_edits": requested,
        "atomic_identity_product": atomic,
        "retry_origin_policy": "exact_original_source_only",
        "partial_candidate_policy": "diagnostic_only_never_reuse",
        "source": source_record,
        "scale_guides": guide_records,
        "face_references": face_records,
        "product_references": product_records,
        "packaging_reference_binding": package_bindings,
        "correction_memory_sha256": correction_memory_sha256,
        "correction_rules": correction_rules,
        "generation_prompt_contract": {
            "version": "generation-hard-rules-v1",
            "marker": GENERATION_PROMPT_MARKER,
            "required_headers": list(GENERATION_PROMPT_HEADERS),
            "validated_missing": [],
        },
        "prompt": {
            "path": relative(project_dir, prompt),
            "sha256": sha256_file(prompt),
            "text": prompt_text,
            "text_sha256": prompt_text_sha256(prompt_text),
        },
        "required_image_inputs": [source_record, *guide_records, *face_records, *product_records],
    }
    payload["request_fingerprint"] = canonical_hash(payload)

    request_dir = project_dir / "review" / "image-generation-requests"
    request_stem = f"{args.shot_id}-{getattr(args, 'unit_id', None)}" if getattr(args, "unit_id", None) else args.shot_id
    for prior_path in request_dir.glob(f"{request_stem}-*.authorization.json") if request_dir.is_dir() else []:
        prior = read_json(prior_path)
        if prior.get("request_fingerprint") != payload["request_fingerprint"]:
            continue
        result_value = prior.get("result_receipt")
        if result_value:
            result_path = resolve(project_dir, result_value)
            if result_path.is_file() and read_json(result_path).get("status") == "rejected_diagnostic":
                raise GateError("BLIND_RETRY_BLOCKED: change one auditable plan, prompt or reference input before retrying from the original")

    request_id = payload["request_fingerprint"][:16]
    payload["request_id"] = request_id
    payload["authorized_at"] = now_iso()
    receipt_path = request_dir / f"{request_stem}-{request_id}.authorization.json"
    write_json(receipt_path, payload)
    if unit is not None:
        unit["image_generation_authorization"] = relative(project_dir, receipt_path)
    else:
        shot.setdefault("asset_links", {})["image_generation_authorization"] = relative(project_dir, receipt_path)
    write_json(manifest_path, manifest)
    return payload


def verify_receipt(project_dir: Path, receipt_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = read_json(project_dir / "project.json")
    receipt = read_json(receipt_path)
    local_repair = receipt.get("release_compatibility_lane") == "explicit_local_shot_repair"
    release = assert_current_project(project, project_dir=project_dir, allow_local_repair=local_repair)
    product = read_json(project_dir / "library" / "product_bible.json")
    manifest = read_json(project_dir / "shots" / "shot_manifest.json")
    shot = find_shot(manifest, str(receipt.get("shot_id") or ""))
    unit = find_unit(shot, receipt.get("unit_id"))
    receipt_status_valid = receipt.get("status") == "authorized" or (
        receipt.get("status") == "consumed" and bool(receipt.get("result_receipt"))
    )
    if not receipt_status_valid or receipt.get("bundle_release_id") != release.get("bundle_release_id"):
        raise GateError("IMAGE_AUTHORIZATION_STALE: receipt is not authorized for the current release")
    if receipt.get("product_bible_sha256") != sha256_file(project_dir / "library" / "product_bible.json"):
        raise GateError("IMAGE_AUTHORIZATION_PRODUCT_CHANGED")
    if receipt.get("shot_contract_sha256") != canonical_hash(shot_contract_value(shot)):
        raise GateError("IMAGE_AUTHORIZATION_SHOT_CHANGED")
    if receipt.get("unit_id") and receipt.get("unit_contract_sha256") != canonical_hash(unit_contract_value(unit)):
        raise GateError("IMAGE_AUTHORIZATION_UNIT_CHANGED")
    correction_memory_path = project_dir / "library" / "correction_memory.json"
    if not correction_memory_path.is_file() or receipt.get("correction_memory_sha256") != sha256_file(correction_memory_path):
        raise GateError("IMAGE_AUTHORIZATION_CORRECTION_MEMORY_CHANGED: recompile the Prompt after applying feedback")
    records = [receipt.get("source"), *(receipt.get("scale_guides") or []), *(receipt.get("face_references") or []), *(receipt.get("product_references") or []), *(receipt.get("packaging_reference_binding") or [])]
    for record in records:
        if not isinstance(record, dict):
            raise GateError("IMAGE_AUTHORIZATION_INPUT_INVALID")
        path = resolve(project_dir, record.get("path"))
        if not path.is_file() or record.get("sha256") != sha256_file(path):
            raise GateError(f"IMAGE_AUTHORIZATION_INPUT_CHANGED: {path}")
    prompt_record = receipt.get("prompt") if isinstance(receipt.get("prompt"), dict) else {}
    prompt_path = resolve(project_dir, prompt_record.get("path"))
    prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    if (
        not prompt_path.is_file()
        or prompt_record.get("sha256") != sha256_file(prompt_path)
        or prompt_record.get("text") != prompt_text
        or prompt_record.get("text_sha256") != prompt_text_sha256(prompt_text)
    ):
        raise GateError("IMAGE_AUTHORIZATION_PROMPT_CHANGED")
    if prompt_record.get("text") and GENERATION_PROMPT_MARKER not in prompt_text:
        raise GateError("IMAGE_AUTHORIZATION_GENERATION_RULE_MARKER_MISSING")
    if receipt.get("retry_origin_policy") != "exact_original_source_only" or receipt.get("partial_candidate_policy") != "diagnostic_only_never_reuse":
        raise GateError("IMAGE_AUTHORIZATION_RETRY_POLICY_INVALID")
    if bool(receipt.get("face_references")) and bool(receipt.get("product_references")) and receipt.get("atomic_identity_product") is not True:
        raise GateError("ATOMIC_IDENTITY_PRODUCT_REQUIRED")
    return receipt, project, product, shot


def record_result(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.expanduser().resolve()
    receipt_path = args.authorization.expanduser().resolve()
    receipt, project, product, shot = verify_receipt(project_dir, receipt_path)
    output = args.output.expanduser().resolve()
    qa_path = args.qa.expanduser().resolve()
    if not output.is_file():
        raise GateError(f"GENERATED_OUTPUT_MISSING: {output}")
    if output == resolve(project_dir, (receipt.get("source") or {}).get("path")):
        raise GateError("GENERATED_OUTPUT_EQUALS_SOURCE")
    qa = read_json(qa_path)
    unit = find_unit(shot, receipt.get("unit_id"))
    exact_contract, effective_shot, _ = exact_frame_context(project_dir, shot, unit)
    product_visible = exact_contract.get("product_visibility") != "absent"
    required = {"composition", "source_provenance"}
    if receipt.get("face_references"):
        required.add("identity")
    if "product" in (receipt.get("requested_edits") or []):
        required.add("product")
    if project.get("product_profile") == "durian-daifuku-v2" and product_visible:
        required.update({"scale", "shape", "state_topology", "instance_count", "instance_variation", "surface", "filling", "endpoint"})
    state = effective_shot.get("product_state") if isinstance(effective_shot.get("product_state"), dict) else {}
    packaging_lock = state.get("packaging_lock") if isinstance(state.get("packaging_lock"), dict) else None
    packaging_visible = product_visible and (
        packaging_lock.get("visible") is True
        if packaging_lock is not None
        else state.get("packaging") not in (None, False, "none", "hidden")
    )
    if project.get("product_profile") == "durian-daifuku-v2" and packaging_visible:
        required.add("package_product_geometry")
    try:
        target_count = int((state.get("instance_lock") or {}).get("target_product_count") or state.get("count") or 0)
    except (TypeError, ValueError):
        target_count = 0
    arrangement_required = product_visible and (packaging_visible or state.get("state") == "plated" or target_count > 1)
    if project.get("product_profile") == "durian-daifuku-v2" and arrangement_required:
        required.update({"layout_continuity", "inventory_transition"})
    missing = sorted(key for key in required if qa.get(key) is not True)
    evidence = qa.get("evidence") if isinstance(qa.get("evidence"), dict) else {}
    for key in sorted(required):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            missing.append(f"{key}_evidence")

    scale_measurement: dict[str, Any] | None = None
    shape_qa_record: dict[str, Any] | None = None
    topology_qa_record: dict[str, Any] | None = None
    instance_qa_record: dict[str, Any] | None = None
    continuity_qa_record: dict[str, Any] | None = None
    surface_qa_record: dict[str, Any] | None = None
    scale_relationship_qa_record: dict[str, Any] | None = None
    if project.get("product_profile") == "durian-daifuku-v2" and product_visible:
        if not args.product_bbox or len(args.product_bbox) != 4:
            missing.append("measured_product_bbox")
        else:
            with Image.open(output) as image:
                width, height = image.size
            x, y, box_width, box_height = [int(value) for value in args.product_bbox]
            # Resolve scale tolerance from the exact delivery unit. A shot may
            # contain multiple SRC/ADD units with different pixel plans.
            plan = ((effective_shot.get("product_state") or {}).get("scale_lock") or {}).get("pixel_plan") or {}
            tolerance = ((plan.get("target") or {}).get("width_tolerance_px") or [])
            bbox_valid = x >= 0 and y >= 0 and box_width > 0 and box_height > 0 and x + box_width <= width and y + box_height <= height
            scale_valid = len(tolerance) == 2 and int(tolerance[0]) <= box_width <= int(tolerance[1])
            scale_measurement = {
                "bbox_xywh": [x, y, box_width, box_height],
                "output_size_px": [width, height],
                "allowed_width_px": tolerance,
                "bbox_valid": bbox_valid,
                "scale_valid": scale_valid,
            }
            if not bbox_valid or not scale_valid:
                missing.append("measured_product_scale")

        if requires_hand_scale_relationship(effective_shot):
            raw_relationship_qa = getattr(args, "scale_relationship_qa", None)
            if raw_relationship_qa is None:
                missing.append("hand_scale_relationship_qa")
            else:
                relationship_path = raw_relationship_qa.expanduser().resolve()
                relationship = read_json(relationship_path) if relationship_path.is_file() else {}
                candidate_record = relationship.get("candidate") if isinstance(relationship.get("candidate"), dict) else {}
                anchors = relationship.get("anchors") if isinstance(relationship.get("anchors"), list) else []
                relationship_valid = (
                    relationship.get("schema_version") == "durian-daifuku-scale-relationship-qa-v1.0"
                    and relationship.get("pass") is True
                    and relationship.get("error_code") is None
                    and relationship.get("anchor_count") == len(anchors)
                    and len(anchors) >= 2
                    and all(isinstance(item, dict) and item.get("pass") is True for item in anchors)
                    and candidate_record.get("sha256") == sha256_file(output)
                    and relationship.get("product_bbox_xywh") == [int(value) for value in (args.product_bbox or [])]
                )
                if not relationship_valid:
                    missing.append(relationship.get("error_code") or "DAIFUKU_SCALE_ANCHOR_CONFLICT")
                scale_relationship_qa_record = {
                    "path": relative(project_dir, relationship_path),
                    "sha256": sha256_file(relationship_path) if relationship_path.is_file() else None,
                    "anchor_count": len(anchors),
                    "audit_valid": relationship_valid,
                }

        raw_surface_qa = getattr(args, "surface_qa", None)
        if raw_surface_qa is None:
            missing.append("pixel_surface_qa")
        else:
            surface_path = raw_surface_qa.expanduser().resolve()
            surface = read_json(surface_path) if surface_path.is_file() else {}
            candidate_record = surface.get("candidate") if isinstance(surface.get("candidate"), dict) else {}
            reference_record = surface.get("approved_reference") if isinstance(surface.get("approved_reference"), dict) else {}
            surface_asset = next((item for item in product.get("reference_assets") or [] if isinstance(item, dict) and item.get("id") == "DF2-SURFACE-01"), {})
            approved_reference = resolve(project_dir, surface_asset.get("target_path"))
            surface_valid = (
                surface.get("schema_version") == "durian-daifuku-surface-qa-v1.0"
                and surface.get("pass") is True
                and (surface.get("color_check") or {}).get("pass") is True
                and (surface.get("texture_check") or {}).get("pass") is True
                and candidate_record.get("sha256") == sha256_file(output)
                and candidate_record.get("bbox_xywh") == [int(value) for value in (args.product_bbox or [])]
                and approved_reference.is_file()
                and reference_record.get("sha256") == sha256_file(approved_reference)
                and surface_asset.get("sha256") in {None, sha256_file(approved_reference)}
            )
            if not surface_valid:
                missing.extend(surface.get("failed_checks") or ["pixel_surface_color_or_texture"])
            surface_qa_record = {"path": relative(project_dir, surface_path), "sha256": sha256_file(surface_path) if surface_path.is_file() else None, "audit_valid": surface_valid}

        raw_topology_qa = getattr(args, "topology_qa", None)
        if raw_topology_qa is None:
            missing.append("state_topology_qa")
        else:
            topology_path = raw_topology_qa.expanduser().resolve()
            topology = read_json(topology_path) if topology_path.is_file() else {}
            forbidden_flags = ("large_excavated_crater", "peeled_top_cap", "scooped_hollow", "open_basin", "hand_torn_hole_as_bite")
            topology_valid = (
                topology.get("schema_version") == "durian-daifuku-topology-qa-v1.0"
                and topology.get("state") == state.get("state")
                and topology.get("pass") is True
                and all(topology.get(flag) is False for flag in forbidden_flags)
            )
            if state.get("state") == "bitten":
                try:
                    removed_ratio = float(topology.get("removed_mass_ratio"))
                except (TypeError, ValueError):
                    removed_ratio = -1.0
                topology_valid = topology_valid and (
                    topology.get("opening_origin") == "teeth_contact"
                    and topology.get("single_localized_concave_notch") is True
                    and isinstance(topology.get("tooth_compression_evidence"), str)
                    and bool(topology.get("tooth_compression_evidence", "").strip())
                    and 0.15 <= removed_ratio <= 0.30
                )
                if shot.get("visual_type") == "person_eating":
                    topology_valid = topology_valid and (
                        topology.get("opening_direction") == "toward_mouth_and_person"
                        and topology.get("mouth_contacts_opening_side") is True
                        and topology.get("camera_facing_opening") is False
                        and isinstance(topology.get("orientation_evidence"), str)
                        and bool(topology.get("orientation_evidence", "").strip())
                    )
            if not topology_valid:
                missing.append("state_topology_geometry")
            topology_qa_record = {
                "path": relative(project_dir, topology_path),
                "sha256": sha256_file(topology_path) if topology_path.is_file() else None,
                "state": topology.get("state"),
                "audit_valid": topology_valid,
            }

        raw_instance_qa = getattr(args, "instance_qa", None)
        if raw_instance_qa is None:
            missing.append("instance_inventory_qa")
        else:
            instance_path = raw_instance_qa.expanduser().resolve()
            instance_qa = read_json(instance_path) if instance_path.is_file() else {}
            lock = state.get("instance_lock") if isinstance(state.get("instance_lock"), dict) else {}
            instances = instance_qa.get("instances") if isinstance(instance_qa.get("instances"), list) else []
            ids = [str(item.get("instance_id") or "") for item in instances if isinstance(item, dict)]
            variants = [str(item.get("shape_variant_id") or "") for item in instances if isinstance(item, dict)]
            instance_valid = (
                instance_qa.get("schema_version") == "durian-daifuku-instance-qa-v1.0"
                and instance_qa.get("source_product_count") == lock.get("source_product_count")
                and instance_qa.get("target_product_count") == lock.get("target_product_count")
                and len(instances) == target_count
                and len(ids) == target_count
                and all(ids)
                and len(set(ids)) == target_count
                and ids == [str(value) for value in lock.get("instance_ids") or []]
                and len(variants) == target_count
                and all(variants)
                and (target_count <= 1 or len(set(variants)) == target_count)
                and instance_qa.get("pixel_identical_clones") is False
                and all(item.get("size_class_pass") is True and item.get("pass") is True for item in instances if isinstance(item, dict))
            )
            if state.get("state") == "held" and target_count > 1:
                instance_valid = instance_valid and all(item.get("contact_deformation") == "slight_local_flattening_with_volume_conservation" for item in instances)
            if not instance_valid:
                missing.append("instance_inventory_or_variation")
            instance_qa_record = {
                "path": relative(project_dir, instance_path),
                "sha256": sha256_file(instance_path) if instance_path.is_file() else None,
                "accounted_product_count": len(instances),
                "audit_valid": instance_valid,
            }

        if arrangement_required:
            raw_continuity_qa = getattr(args, "continuity_qa", None)
            if raw_continuity_qa is None:
                missing.append("layout_inventory_qa")
            else:
                continuity_path = raw_continuity_qa.expanduser().resolve()
                continuity = read_json(continuity_path) if continuity_path.is_file() else {}
                layout_lock = state.get("arrangement_lock") if isinstance(state.get("arrangement_lock"), dict) else {}
                continuity_valid = (
                    continuity.get("schema_version") == "durian-daifuku-continuity-qa-v1.0"
                    and continuity.get("layout_id") == layout_lock.get("layout_id")
                    and continuity.get("container_id") == layout_lock.get("container_id")
                    and continuity.get("instance_ids") == layout_lock.get("instance_ids")
                    and continuity.get("relative_topology_preserved") is True
                    and continuity.get("inventory_transition_valid") is True
                    and continuity.get("pass") is True
                )
                if packaging_visible:
                    continuity_valid = continuity_valid and (
                        continuity.get("perfect_grid") is False
                        and continuity.get("equal_spacing") is False
                        and continuity.get("uniform_orientation") is False
                        and continuity.get("natural_irregularity_visible") is True
                    )
                if not continuity_valid:
                    missing.append("layout_inventory_continuity")
                continuity_qa_record = {
                    "path": relative(project_dir, continuity_path),
                    "sha256": sha256_file(continuity_path) if continuity_path.is_file() else None,
                    "layout_id": continuity.get("layout_id"),
                    "audit_valid": continuity_valid,
                }

        if packaging_visible:
            raw_shape_qa = getattr(args, "shape_qa", None)
            if raw_shape_qa is None:
                missing.append("packaged_instance_shape_qa")
            else:
                shape_qa_path = raw_shape_qa.expanduser().resolve()
                if not shape_qa_path.is_file():
                    missing.append("packaged_instance_shape_qa_file")
                else:
                    shape_qa = read_json(shape_qa_path)
                    contract = product.get("shape_contract") if isinstance(product.get("shape_contract"), dict) else {}
                    expected_identity = contract.get("geometry_identity_id")
                    expected_count = int(contract.get("package_capacity_count") or 4)
                    instances = shape_qa.get("instances") if isinstance(shape_qa.get("instances"), list) else []
                    instance_ids = [str(item.get("instance_id") or "") for item in instances if isinstance(item, dict)]
                    audit_valid = (
                        shape_qa.get("schema_version") == "durian-daifuku-shape-qa-v1.0"
                        and shape_qa.get("geometry_identity_id") == expected_identity
                        and shape_qa.get("container_shape_inheritance") is False
                        and shape_qa.get("cross_context_match") is True
                        and shape_qa.get("accounted_product_count") == expected_count
                        and len(instances) == expected_count
                        and len(instance_ids) == expected_count
                        and all(instance_ids)
                        and len(set(instance_ids)) == expected_count
                    )
                    substantially_visible = 0
                    for item in instances:
                        if not isinstance(item, dict) or item.get("visibility") not in {"visible", "partial", "occluded", "removed"}:
                            audit_valid = False
                            continue
                        if not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
                            audit_valid = False
                        if item.get("visibility") in {"visible", "partial"}:
                            substantially_visible += 1
                            try:
                                aspect_ratio = float(item.get("bbox_aspect_ratio"))
                                straight_fraction = float(item.get("straight_edge_fraction"))
                                corner_count = int(item.get("right_angle_corner_count"))
                            except (TypeError, ValueError):
                                audit_valid = False
                                continue
                            audit_valid = audit_valid and (
                                item.get("geometry_identity_id") == expected_identity
                                and item.get("silhouette_family") == "rounded_slightly_oblate"
                                and item.get("pass") is True
                                and 0.8 <= aspect_ratio <= 1.25
                                and straight_fraction <= float(contract.get("maximum_straight_edge_fraction") or 0.18)
                                and corner_count == 0
                            )
                    if substantially_visible < 1:
                        audit_valid = False
                    if not audit_valid:
                        missing.append("packaged_instance_shape_geometry")
                    shape_qa_record = {
                        "path": relative(project_dir, shape_qa_path),
                        "sha256": sha256_file(shape_qa_path),
                        "geometry_identity_id": shape_qa.get("geometry_identity_id"),
                        "accounted_product_count": shape_qa.get("accounted_product_count"),
                        "substantially_visible_count": substantially_visible,
                        "audit_valid": audit_valid,
                    }

    missing = sorted(set(missing))
    status = "approved_candidate" if not missing else "rejected_diagnostic"
    result = {
        "schema_version": "image-generation-result-v1.0",
        "status": status,
        "request_id": receipt.get("request_id"),
        "request_fingerprint": receipt.get("request_fingerprint"),
        "authorization_path": relative(project_dir, receipt_path),
        "shot_id": receipt.get("shot_id"),
        "unit_id": receipt.get("unit_id"),
        "exact_first_frame_product_visibility": exact_contract.get("product_visibility"),
        "bundle_release_id": receipt.get("bundle_release_id"),
        "atomic_identity_product": receipt.get("atomic_identity_product"),
        "output": {"path": relative(project_dir, output), "sha256": sha256_file(output)},
        "joint_qa": {key: qa.get(key, "not_applicable") for key in JOINT_QA_KEYS},
        "joint_qa_evidence": {key: evidence.get(key) for key in JOINT_QA_KEYS if key in required},
        "scale_measurement": scale_measurement,
        "scale_relationship_qa": scale_relationship_qa_record,
        "shape_qa": shape_qa_record,
        "topology_qa": topology_qa_record,
        "instance_qa": instance_qa_record,
        "continuity_qa": continuity_qa_record,
        "surface_qa": surface_qa_record,
        "failed_checks": missing,
        "retry_instruction": "return_to_exact_original_source" if missing else "none",
        "partial_candidate_reusable": False if missing else None,
        "recorded_at": now_iso(),
    }
    result_path = receipt_path.with_name(receipt_path.name.replace(".authorization.json", ".result.json"))
    write_json(result_path, result)
    receipt["result_receipt"] = relative(project_dir, result_path)
    receipt["status"] = "consumed"
    write_json(receipt_path, receipt)
    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = read_json(manifest_path)
    current_shot = find_shot(manifest, str(receipt.get("shot_id")))
    current_unit = find_unit(current_shot, receipt.get("unit_id"))
    assets = current_unit if current_unit is not None else current_shot.setdefault("asset_links", {})
    assets["image_generation_result_receipt"] = relative(project_dir, result_path)
    if status == "approved_candidate":
        assets["candidate_generation_first_frame"] = relative(project_dir, output)
    else:
        assets.pop("candidate_generation_first_frame", None)
        if assets.get("approved_generation_first_frame") == relative(project_dir, output):
            assets["approved_generation_first_frame"] = None
    write_json(manifest_path, manifest)
    return result


def promote_user_approved_result(args: argparse.Namespace) -> dict[str, Any]:
    """Promote one approved candidate into the canonical shot/unit manifest.

    The old workflow required a person to edit ``approved_generation_first_frame``
    by hand.  This command makes that transition explicit, byte-bound and
    reversible: rejected/partial candidates or an approval for different bytes
    cannot enter the canonical generation inputs.
    """
    project_dir = args.project_dir.expanduser().resolve()
    receipt_path = args.authorization.expanduser().resolve()
    receipt, project, _product, shot = verify_receipt(project_dir, receipt_path)
    result_value = receipt.get("result_receipt")
    result_path = resolve(project_dir, result_value)
    if not result_path.is_file():
        raise GateError("APPROVED_RESULT_RECEIPT_MISSING: record-result must finish before promotion")
    result = read_json(result_path)
    if result.get("status") != "approved_candidate":
        raise GateError("PARTIAL_CANDIDATE_PROMOTION_BLOCKED: only approved_candidate results may be promoted")
    output_record = result.get("output") if isinstance(result.get("output"), dict) else {}
    output = resolve(project_dir, output_record.get("path"))
    output_sha = output_record.get("sha256")
    if not output.is_file() or output_sha != sha256_file(output):
        raise GateError("APPROVED_FRAME_RESULT_HASH_MISMATCH: candidate bytes changed before promotion")

    approval_path = args.approval.expanduser().resolve()
    approval = read_json(approval_path)
    if approval.get("status") != "user_approved":
        raise GateError("USER_APPROVAL_REQUIRED: approval receipt status must equal user_approved")
    display_receipt_id = approval.get("display_receipt_id")
    if not isinstance(display_receipt_id, str) or not display_receipt_id.strip():
        raise GateError("USER_APPROVAL_RECEIPT_ID_MISSING: record the gallery receipt shown to the user")
    asset_sha = approval.get("asset_sha256") or approval.get("output_sha256")
    asset_refs = approval.get("asset_refs") if isinstance(approval.get("asset_refs"), list) else []
    if not asset_sha:
        matches = [item for item in asset_refs if isinstance(item, dict) and item.get("sha256") == output_sha]
        if len(matches) == 1:
            asset_sha = matches[0].get("sha256")
    if asset_sha != output_sha:
        raise GateError("USER_APPROVAL_HASH_MISMATCH: gallery approval must bind the exact candidate bytes")
    if approval.get("request_id") not in (None, receipt.get("request_id")):
        raise GateError("USER_APPROVAL_REQUEST_MISMATCH")
    if approval.get("request_fingerprint") not in (None, receipt.get("request_fingerprint")):
        raise GateError("USER_APPROVAL_REQUEST_MISMATCH")

    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = read_json(manifest_path)
    current_shot = find_shot(manifest, str(receipt.get("shot_id")))
    current_unit = find_unit(current_shot, receipt.get("unit_id"))
    assets = current_unit if current_unit is not None else current_shot.setdefault("asset_links", {})
    output_relative = relative(project_dir, output)
    approval_relative = relative(project_dir, approval_path)
    assets["approved_generation_first_frame"] = output_relative
    assets["approval_status"] = "user_approved"
    assets["user_approval"] = {
        "status": "user_approved",
        "display_receipt_id": display_receipt_id,
        "approved_at": approval.get("approved_at") or now_iso(),
        "asset_sha256": output_sha,
        "request_id": receipt.get("request_id"),
        "request_fingerprint": receipt.get("request_fingerprint"),
        "gallery_receipt": approval_relative,
    }
    promotion = {
        "schema_version": "image-generation-promotion-v1.0",
        "status": "promoted_to_canonical",
        "request_id": receipt.get("request_id"),
        "request_fingerprint": receipt.get("request_fingerprint"),
        "shot_id": receipt.get("shot_id"),
        "unit_id": receipt.get("unit_id"),
        "result_receipt": relative(project_dir, result_path),
        "approval_receipt": approval_relative,
        "output": {"path": output_relative, "sha256": output_sha},
        "promoted_at": now_iso(),
    }
    promotion_path = result_path.with_name(result_path.name.replace(".result.json", ".promotion.json"))
    write_json(promotion_path, promotion)
    assets["approved_generation_promotion_receipt"] = relative(project_dir, promotion_path)
    write_json(manifest_path, manifest)
    return promotion


def validate_approved_result_binding(project_dir: Path, project: dict[str, Any], shot: dict[str, Any]) -> list[tuple[str, str]]:
    """Return pipeline issue-code/detail pairs for an approved first-frame binding."""
    assets = shot.get("asset_links") if isinstance(shot.get("asset_links"), dict) else {}
    approved_value = assets.get("approved_generation_first_frame")
    if not approved_value:
        return []
    receipt_value = assets.get("image_generation_result_receipt")
    if not receipt_value:
        return [("IMAGE_GENERATION_RESULT_RECEIPT_MISSING", "Approved first frames require a hash-bound joint result receipt.")]
    result_path = resolve(project_dir, receipt_value)
    if not result_path.is_file():
        return [("IMAGE_GENERATION_RESULT_RECEIPT_UNAVAILABLE", f"Result receipt is unavailable: {result_path}")]
    result = read_json(result_path)
    approved = resolve(project_dir, approved_value)
    failures: list[tuple[str, str]] = []
    if result.get("status") != "approved_candidate":
        failures.append(("PARTIAL_CANDIDATE_PROMOTION_BLOCKED", "Rejected or partial candidates cannot be promoted or reused."))
    if not approved.is_file() or (result.get("output") or {}).get("sha256") != sha256_file(approved):
        failures.append(("APPROVED_FRAME_RESULT_HASH_MISMATCH", "Approved frame bytes must match the joint result receipt."))
    if result.get("atomic_identity_product") is True:
        qa = result.get("joint_qa") if isinstance(result.get("joint_qa"), dict) else {}
        if qa.get("identity") is not True or qa.get("product") is not True:
            failures.append(("ATOMIC_IDENTITY_PRODUCT_QA_FAILED", "Identity and product must both pass on the same candidate."))
    shape_qa = result.get("shape_qa") if isinstance(result.get("shape_qa"), dict) else None
    if shape_qa:
        shape_qa_path = resolve(project_dir, shape_qa.get("path"))
        if not shape_qa_path.is_file() or shape_qa.get("sha256") != sha256_file(shape_qa_path) or shape_qa.get("audit_valid") is not True:
            failures.append(("DAIFUKU_PACKAGE_SHAPE_QA_BINDING_INVALID", "Packaged-product per-instance shape QA must remain hash-bound and valid."))
    for field, code in (
        ("scale_relationship_qa", "DAIFUKU_SCALE_RELATIONSHIP_QA_BINDING_INVALID"),
        ("topology_qa", "DAIFUKU_TOPOLOGY_QA_BINDING_INVALID"),
        ("instance_qa", "DAIFUKU_INSTANCE_QA_BINDING_INVALID"),
        ("continuity_qa", "DAIFUKU_CONTINUITY_QA_BINDING_INVALID"),
        ("surface_qa", "DAIFUKU_SURFACE_QA_BINDING_INVALID"),
    ):
        record = result.get(field) if isinstance(result.get(field), dict) else None
        if not record:
            if field not in {"continuity_qa", "scale_relationship_qa"} or (field == "scale_relationship_qa" and requires_hand_scale_relationship(shot)):
                failures.append((code, f"Required {field} receipt is missing."))
            continue
        record_path = resolve(project_dir, record.get("path"))
        if not record_path.is_file() or record.get("sha256") != sha256_file(record_path) or record.get("audit_valid") is not True:
            failures.append((code, f"{field} must remain hash-bound and valid."))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed image generation authorization and joint result gate.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    authorize_parser = subparsers.add_parser("authorize")
    authorize_parser.add_argument("--project-dir", required=True, type=Path)
    authorize_parser.add_argument("--shot-id", required=True)
    authorize_parser.add_argument("--unit-id", help="Bind authorization to one SRC/ADD delivery unit inside the shot.")
    authorize_parser.add_argument("--prompt-file", required=True, type=Path)
    authorize_parser.add_argument("--edit", action="append", choices=("identity", "product"), default=[])
    authorize_parser.add_argument("--atomic", action="store_true")
    authorize_parser.add_argument("--face-reference", action="append", default=[])
    authorize_parser.add_argument(
        "--local-repair",
        action="store_true",
        help="Explicitly authorize one SRC/ADD retry for a project locked to an older compatible release; never upgrades the project globally.",
    )
    result_parser = subparsers.add_parser("record-result")
    result_parser.add_argument("--project-dir", required=True, type=Path)
    result_parser.add_argument("--authorization", required=True, type=Path)
    result_parser.add_argument("--output", required=True, type=Path)
    result_parser.add_argument("--qa", required=True, type=Path)
    result_parser.add_argument("--product-bbox", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    result_parser.add_argument("--shape-qa", type=Path, help="Machine-readable per-instance packaged-daifuku silhouette audit.")
    result_parser.add_argument("--topology-qa", type=Path, help="Machine-readable state topology and bite-integrity audit.")
    result_parser.add_argument("--instance-qa", type=Path, help="Machine-readable product-count, size-class and natural-variation audit.")
    result_parser.add_argument("--continuity-qa", type=Path, help="Machine-readable layout and inventory-transition audit for multi/container/package shots.")
    result_parser.add_argument("--surface-qa", type=Path, help="Hash-bound shell color and stone-texture pixel audit.")
    result_parser.add_argument("--scale-relationship-qa", type=Path, help="Hash-bound two-anchor hand/product size relationship audit.")
    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--project-dir", required=True, type=Path)
    promote_parser.add_argument("--authorization", required=True, type=Path)
    promote_parser.add_argument("--approval", required=True, type=Path, help="User-visible gallery receipt with status=user_approved.")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--project-dir", required=True, type=Path)
    verify_parser.add_argument("--authorization", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "authorize":
            result = authorize(args)
        elif args.command == "record-result":
            result = record_result(args)
        elif args.command == "promote":
            result = promote_user_approved_result(args)
        else:
            receipt, _, _, _ = verify_receipt(args.project_dir.expanduser().resolve(), args.authorization.expanduser().resolve())
            result = {"status": "authorized", "request_id": receipt.get("request_id")}
    except (GateError, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
