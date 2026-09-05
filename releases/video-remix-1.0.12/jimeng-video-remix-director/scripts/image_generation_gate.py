#!/usr/bin/env python3
"""Authorize and record image generation without allowing legacy or half-edit bypasses."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


SKILL_DIR = Path(__file__).resolve().parent.parent
RELEASE_PATH = SKILL_DIR / "references" / "skill-release.json"
IMAGE_EXECUTION_TIERS = {"first_frame_only", "full_delivery"}
JOINT_QA_KEYS = ("identity", "product", "scale", "shape", "state_topology", "instance_count", "instance_variation", "layout_continuity", "inventory_transition", "package_product_geometry", "surface", "filling", "endpoint", "composition", "source_provenance")


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
    return value


def load_current_release() -> dict[str, Any]:
    return read_json(RELEASE_PATH)


def assert_current_project(project: dict[str, Any], *, require_image_tier: bool = True) -> dict[str, Any]:
    release = load_current_release()
    lock = project.get("skill_release_lock") if isinstance(project.get("skill_release_lock"), dict) else {}
    current_id = release.get("bundle_release_id")
    locked_id = lock.get("bundle_release_id")
    if locked_id != current_id:
        raise GateError(
            f"LEGACY_PROJECT_GENERATION_BLOCKED: project is locked to {locked_id or 'unmanaged-legacy'}, "
            f"current release is {current_id}; create an explicit non-destructive migration copy first"
        )
    if lock.get("prompt_authoring_contract") != release.get("prompt_authoring_contract") or lock.get("auto_upgrade") is not False:
        raise GateError("EXPLICIT_MIGRATION_REQUIRED: release lock does not match the current immutable authoring contract")
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


def hash_records(project_dir: Path, paths: list[Path], role: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            raise GateError(f"BOUND_REFERENCE_UNAVAILABLE: {path}")
        records.append({"path": relative(project_dir, path), "sha256": sha256_file(path), "role": role})
    return records


def validate_pixel_plan(project_dir: Path, project: dict[str, Any], product: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any] | None:
    if project.get("product_profile") != "durian-daifuku-v2":
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
    if any(binding.get(key) != value for key, value in expected.items()):
        raise GateError("DAIFUKU_PIXEL_CONTRACT_STALE: release, product, state or anchor changed after pixel preflight")
    pixel_anchor = plan.get("anchor") if isinstance(plan.get("anchor"), dict) else {}
    box = pixel_anchor.get("measurement_bbox_xywh")
    if pixel_anchor.get("measurement_method") != "annotated_bbox" or not isinstance(box, list) or len(box) != 4:
        raise GateError("DAIFUKU_ANCHOR_MEASUREMENT_UNPROVEN: use an annotated in-frame anchor bbox")
    if pixel_anchor.get("type") != anchor.get("type") or pixel_anchor.get("expected_ratio") != anchor.get("expected_ratio"):
        raise GateError("DAIFUKU_PIXEL_ANCHOR_MISMATCH: plan anchor must equal the shot scale lock")
    return plan


def authorize(args: argparse.Namespace) -> dict[str, Any]:
    project_dir = args.project_dir.expanduser().resolve()
    project = read_json(project_dir / "project.json")
    release = assert_current_project(project)
    product = read_json(project_dir / "library" / "product_bible.json")
    manifest_path = project_dir / "shots" / "shot_manifest.json"
    manifest = read_json(manifest_path)
    shot = find_shot(manifest, args.shot_id)
    source = resolve(project_dir, (shot.get("asset_links") or {}).get("source_first_frame"))
    prompt = args.prompt_file.expanduser().resolve()
    if not source.is_file():
        raise GateError("ORIGINAL_SOURCE_FRAME_MISSING: retries cannot fall back to a generated candidate")
    if not prompt.is_file() or not prompt.read_text(encoding="utf-8").strip():
        raise GateError("IMAGE_PROMPT_MISSING: persist the exact submitted prompt before authorization")

    face_paths = avatar_reference_paths(project_dir, shot, args.face_reference or [])
    product_paths = product_reference_paths(project_dir, project, shot)
    identity_required = bool(face_paths)
    product_required = project.get("product_mode") == "replace_product"
    requested = sorted(set(args.edit or []))
    expected_edits = sorted([name for name, needed in (("identity", identity_required), ("product", product_required)) if needed])
    if requested != expected_edits:
        raise GateError(f"ATOMIC_EDIT_SET_MISMATCH: requested edits must be exactly {expected_edits}, got {requested}")
    atomic = identity_required and product_required
    if atomic and args.atomic is not True:
        raise GateError("ATOMIC_IDENTITY_PRODUCT_REQUIRED: identity and product replacement must run in one request")
    if not atomic and args.atomic is True:
        raise GateError("ATOMIC_FLAG_INVALID: atomic is reserved for shots that require both identity and product")

    pixel_plan = validate_pixel_plan(project_dir, project, product, shot)
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
        "prompt_authoring_contract": release.get("prompt_authoring_contract"),
        "project_id": project.get("project_id"),
        "shot_id": args.shot_id,
        "product_profile": project.get("product_profile"),
        "product_version": product.get("version"),
        "product_bible_sha256": sha256_file(project_dir / "library" / "product_bible.json"),
        "shot_contract_sha256": canonical_hash(shot_contract_value(shot)),
        "requested_edits": requested,
        "atomic_identity_product": atomic,
        "retry_origin_policy": "exact_original_source_only",
        "partial_candidate_policy": "diagnostic_only_never_reuse",
        "source": source_record,
        "scale_guides": guide_records,
        "face_references": face_records,
        "product_references": product_records,
        "prompt": {"path": relative(project_dir, prompt), "sha256": sha256_file(prompt)},
        "required_image_inputs": [source_record, *guide_records, *face_records, *product_records],
    }
    payload["request_fingerprint"] = canonical_hash(payload)

    request_dir = project_dir / "review" / "image-generation-requests"
    for prior_path in request_dir.glob(f"{args.shot_id}-*.authorization.json") if request_dir.is_dir() else []:
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
    receipt_path = request_dir / f"{args.shot_id}-{request_id}.authorization.json"
    write_json(receipt_path, payload)
    shot.setdefault("asset_links", {})["image_generation_authorization"] = relative(project_dir, receipt_path)
    write_json(manifest_path, manifest)
    return payload


def verify_receipt(project_dir: Path, receipt_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = read_json(project_dir / "project.json")
    release = assert_current_project(project)
    product = read_json(project_dir / "library" / "product_bible.json")
    manifest = read_json(project_dir / "shots" / "shot_manifest.json")
    receipt = read_json(receipt_path)
    shot = find_shot(manifest, str(receipt.get("shot_id") or ""))
    if receipt.get("status") != "authorized" or receipt.get("bundle_release_id") != release.get("bundle_release_id"):
        raise GateError("IMAGE_AUTHORIZATION_STALE: receipt is not authorized for the current release")
    if receipt.get("product_bible_sha256") != sha256_file(project_dir / "library" / "product_bible.json"):
        raise GateError("IMAGE_AUTHORIZATION_PRODUCT_CHANGED")
    if receipt.get("shot_contract_sha256") != canonical_hash(shot_contract_value(shot)):
        raise GateError("IMAGE_AUTHORIZATION_SHOT_CHANGED")
    records = [receipt.get("source"), *(receipt.get("scale_guides") or []), *(receipt.get("face_references") or []), *(receipt.get("product_references") or [])]
    for record in records:
        if not isinstance(record, dict):
            raise GateError("IMAGE_AUTHORIZATION_INPUT_INVALID")
        path = resolve(project_dir, record.get("path"))
        if not path.is_file() or record.get("sha256") != sha256_file(path):
            raise GateError(f"IMAGE_AUTHORIZATION_INPUT_CHANGED: {path}")
    prompt_path = resolve(project_dir, (receipt.get("prompt") or {}).get("path"))
    if not prompt_path.is_file() or (receipt.get("prompt") or {}).get("sha256") != sha256_file(prompt_path):
        raise GateError("IMAGE_AUTHORIZATION_PROMPT_CHANGED")
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
    required = {"composition", "source_provenance"}
    if receipt.get("face_references"):
        required.add("identity")
    if receipt.get("product_references"):
        required.add("product")
    if project.get("product_profile") == "durian-daifuku-v2":
        required.update({"scale", "shape", "state_topology", "instance_count", "instance_variation", "surface", "filling", "endpoint"})
    state = shot.get("product_state") if isinstance(shot.get("product_state"), dict) else {}
    packaging_visible = state.get("packaging") not in (None, False, "none", "hidden")
    if project.get("product_profile") == "durian-daifuku-v2" and packaging_visible:
        required.add("package_product_geometry")
    try:
        target_count = int((state.get("instance_lock") or {}).get("target_product_count") or state.get("count") or 0)
    except (TypeError, ValueError):
        target_count = 0
    arrangement_required = packaging_visible or state.get("state") == "plated" or target_count > 1
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
    if project.get("product_profile") == "durian-daifuku-v2":
        if not args.product_bbox or len(args.product_bbox) != 4:
            missing.append("measured_product_bbox")
        else:
            with Image.open(output) as image:
                width, height = image.size
            x, y, box_width, box_height = [int(value) for value in args.product_bbox]
            plan = ((shot.get("product_state") or {}).get("scale_lock") or {}).get("pixel_plan") or {}
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
        "bundle_release_id": receipt.get("bundle_release_id"),
        "atomic_identity_product": receipt.get("atomic_identity_product"),
        "output": {"path": relative(project_dir, output), "sha256": sha256_file(output)},
        "joint_qa": {key: qa.get(key, "not_applicable") for key in JOINT_QA_KEYS},
        "joint_qa_evidence": {key: evidence.get(key) for key in JOINT_QA_KEYS if key in required},
        "scale_measurement": scale_measurement,
        "shape_qa": shape_qa_record,
        "topology_qa": topology_qa_record,
        "instance_qa": instance_qa_record,
        "continuity_qa": continuity_qa_record,
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
    assets = current_shot.setdefault("asset_links", {})
    assets["image_generation_result_receipt"] = relative(project_dir, result_path)
    if status == "approved_candidate":
        assets["candidate_generation_first_frame"] = relative(project_dir, output)
    else:
        assets.pop("candidate_generation_first_frame", None)
        if assets.get("approved_generation_first_frame") == relative(project_dir, output):
            assets["approved_generation_first_frame"] = None
    write_json(manifest_path, manifest)
    return result


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
        ("topology_qa", "DAIFUKU_TOPOLOGY_QA_BINDING_INVALID"),
        ("instance_qa", "DAIFUKU_INSTANCE_QA_BINDING_INVALID"),
        ("continuity_qa", "DAIFUKU_CONTINUITY_QA_BINDING_INVALID"),
    ):
        record = result.get(field) if isinstance(result.get(field), dict) else None
        if not record:
            if field != "continuity_qa":
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
    authorize_parser.add_argument("--prompt-file", required=True, type=Path)
    authorize_parser.add_argument("--edit", action="append", choices=("identity", "product"), default=[])
    authorize_parser.add_argument("--atomic", action="store_true")
    authorize_parser.add_argument("--face-reference", action="append", default=[])
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
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--project-dir", required=True, type=Path)
    verify_parser.add_argument("--authorization", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "authorize":
            result = authorize(args)
        elif args.command == "record-result":
            result = record_result(args)
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
