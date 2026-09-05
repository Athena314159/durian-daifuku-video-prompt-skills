#!/usr/bin/env python3
"""Regression tests for legacy blocking, atomic edits and original-source retries."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from image_generation_gate import GateError, authorize, packaging_reference_binding, promote_user_approved_result, record_result, validate_approved_result_binding  # noqa: E402
from init_project import initialize_project, load_json, write_json  # noqa: E402
from prepare_daifuku_pixel_preflight import prepare  # noqa: E402
from test_durian_daifuku_v2_contract import opening_shot  # noqa: E402


def must_block(callable_value, code: str) -> None:
    try:
        callable_value()
    except GateError as exc:
        assert code in str(exc), (code, str(exc))
    else:
        raise AssertionError(f"Expected {code}")


def authorization_args(project_dir: Path, prompt: Path, face: Path, *, edits: list[str], atomic: bool, unit_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        project_dir=project_dir,
        shot_id="S001",
        unit_id=unit_id,
        prompt_file=prompt,
        edit=edits,
        atomic=atomic,
        face_reference=[str(face)],
    )


VALID_REPLACEMENT_PROMPT = """【生成目标与叙事职责】
从同一原始首帧完成原子换脸与产品替换。
【产品与动作物理】
目标产品约7厘米手工榴莲冰皮大福 durian-daifuku-v2；暖奶白细糯米粉雾皮，连续暖金黄果泥。
【生图硬性规则】
GENERATION_HARD_RULES_V1；原产品全部清除；人物换脸使用授权身份参考；无字幕、无水印；严格从同一原图生成。
"""


VALID_PACKAGING_PROMPT = VALID_REPLACEMENT_PROMPT + "只使用批准包装母版投射当前可见包装面。"


VALID_ABSENT_PROMPT = """【生成目标与叙事职责】
从同一原始首帧只换人物身份。
【产品与动作物理】
精确首帧不提前出现产品，产品0。
【生图硬性规则】
GENERATION_HARD_RULES_V1；人物换脸使用授权身份参考；无字幕、无水印；严格从同一原图生成。
"""


def test_record_result_scale_plan_is_unit_scoped() -> None:
    source = (SCRIPT_DIR / "image_generation_gate.py").read_text(encoding="utf-8")
    assert 'plan = ((effective_shot.get("product_state") or {}).get("scale_lock") or {}).get("pixel_plan") or {}' in source
    assert 'plan = ((shot.get("product_state") or {}).get("scale_lock") or {}).get("pixel_plan") or {}' not in source


def write_state_audits(project_dir: Path, state: dict, stem: str, *, packaging: bool = False) -> tuple[Path, Path, Path | None]:
    root = project_dir / "review" / "candidates"
    topology = root / f"{stem}.topology.json"
    write_json(
        topology,
        {
            "schema_version": "durian-daifuku-topology-qa-v1.0",
            "state": state["state"],
            "large_excavated_crater": False,
            "peeled_top_cap": False,
            "scooped_hollow": False,
            "open_basin": False,
            "hand_torn_hole_as_bite": False,
            "pass": True,
        },
    )
    lock = state["instance_lock"]
    instance = root / f"{stem}.instances.json"
    write_json(
        instance,
        {
            "schema_version": "durian-daifuku-instance-qa-v1.0",
            "source_product_count": lock["source_product_count"],
            "target_product_count": lock["target_product_count"],
            "pixel_identical_clones": False,
            "instances": [
                {
                    "instance_id": instance_id,
                    "shape_variant_id": variant_id,
                    "size_class_pass": True,
                    "contact_deformation": lock.get("contact_deformation"),
                    "pass": True,
                }
                for instance_id, variant_id in zip(lock["instance_ids"], lock["shape_variant_ids"])
            ],
        },
    )
    continuity = None
    if packaging:
        arrangement = state["arrangement_lock"]
        continuity = root / f"{stem}.continuity.json"
        write_json(
            continuity,
            {
                "schema_version": "durian-daifuku-continuity-qa-v1.0",
                "layout_id": arrangement["layout_id"],
                "container_id": arrangement["container_id"],
                "instance_ids": arrangement["instance_ids"],
                "relative_topology_preserved": True,
                "inventory_transition_valid": True,
                "perfect_grid": False,
                "equal_spacing": False,
                "uniform_orientation": False,
                "natural_irregularity_visible": True,
                "pass": True,
            },
        )
    return topology, instance, continuity


def write_surface_qa(project_dir: Path, product: dict, output: Path, bbox: list[int], stem: str) -> Path:
    surface_asset = next(item for item in product["reference_assets"] if item["id"] == "DF2-SURFACE-01")
    reference = project_dir / surface_asset["target_path"]
    path = project_dir / "review" / "candidates" / f"{stem}.surface.json"
    write_json(path, {
        "schema_version": "durian-daifuku-surface-qa-v1.0",
        "candidate": {"path": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "bbox_xywh": bbox},
        "approved_reference": {"path": str(reference), "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(), "bbox_xywh": [0, 0, 1, 1]},
        "color_check": {"pass": True},
        "texture_check": {"pass": True},
        "pass": True,
        "failed_checks": [],
    })
    return path


def write_scale_relationship_qa(project_dir: Path, output: Path, bbox: list[int], stem: str) -> Path:
    path = project_dir / "review" / "candidates" / f"{stem}.scale-relationship.json"
    write_json(path, {
        "schema_version": "durian-daifuku-scale-relationship-qa-v1.0",
        "candidate": {"path": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "size_px": [800, 1200]},
        "product_bbox_xywh": bbox,
        "minimum_anchor_count": 2,
        "anchor_count": 2,
        "anchors": [
            {"type": "index_finger_mid", "bbox_xywh": [100, 800, 20, 40], "measured_width_px": 20, "product_to_anchor_width_ratio": 4.2, "expected_ratio": [4.0, 4.4], "pass": True},
            {"type": "index_finger_mid", "bbox_xywh": [180, 800, 20, 40], "measured_width_px": 20, "product_to_anchor_width_ratio": 4.2, "expected_ratio": [4.0, 4.4], "pass": True},
        ],
        "pass": True,
        "error_code": None,
    })
    return path


def main() -> int:
    test_record_result_scale_plan_is_unit_scoped()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        project_dir = initialize_project(
            "atomic-image-test",
            root,
            "durian-daifuku-v2",
            "ugc-food-review-v1",
            execution_tier="first_frame_only",
        )
        product = load_json(project_dir / "library" / "product_bible.json")
        shot = opening_shot(product)
        source = project_dir / "source" / "frames" / "S001-source.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1200), (206, 194, 180)).save(source)
        shot["asset_links"]["source_first_frame"] = "source/frames/S001-source.png"

        face = project_dir / "source" / "references" / "avatar-AV-T01" / "front.png"
        face.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (160, 200), (180, 140, 120, 255)).save(face)
        avatar_library = load_json(project_dir / "library" / "avatar_library.json")
        avatar_library["avatars"] = [
            {
                "id": "AV-T01",
                "name": "test avatar",
                "active": True,
                "portrait_rights_cleared": True,
                "usage_scope": "internal_test",
                "reference_assets": {"front": "source/references/avatar-AV-T01/front.png", "expressions": []},
            }
        ]
        write_json(project_dir / "library" / "avatar_library.json", avatar_library)
        shot["asset_links"].update(
            {
                "avatar_reference": "AV-T01",
                "edit_chain": {
                    "face_edit_enabled": True,
                    "face_reference_ids": ["AV-T01"],
                    "atomic_identity_product_required": True,
                    "retry_origin_policy": "exact_original_source_only",
                    "partial_candidate_policy": "diagnostic_only_never_reuse",
                },
            }
        )
        packaging_probe = copy.deepcopy(shot)
        packaging_probe["product_state"]["packaging"] = "visible_retail_box"
        packaging_probe["product_state"]["packaging_lock"] = {
            "visible": True,
            "package_levels": ["retail_outer_box"],
            "visible_faces_by_level": {"retail_outer_box": ["front"]},
            "reference_asset_ids_by_level": {"retail_outer_box": {"front": "DF2-PACK-RETAIL-FRONT-01"}},
            "invented_packaging_allowed": False,
            "artwork_mode": "approved_master_projection_only",
        }
        must_block(
            lambda: packaging_reference_binding(project_dir, load_json(project_dir / "project.json"), product, packaging_probe),
            "DAIFUKU_PACKAGING_REFERENCE_MISSING",
        )
        retail_front = next(item for item in product["reference_assets"] if item["id"] == "DF2-PACK-RETAIL-FRONT-01")
        packaging_probe["asset_links"]["product_references"].append(retail_front["target_path"])
        package_records = packaging_reference_binding(project_dir, load_json(project_dir / "project.json"), product, packaging_probe)
        assert package_records == [{
            "contract_type": "approved_package_face",
            "packaging_level": "retail_outer_box",
            "visible_face": "front",
            "asset_id": "DF2-PACK-RETAIL-FRONT-01",
            "path": retail_front["target_path"],
            "sha256": retail_front["sha256"],
            "source_region": None,
            "projection_required": None,
        }]
        manifest_path = project_dir / "shots" / "shot_manifest.json"
        manifest = load_json(manifest_path)
        manifest["shots"] = [shot]
        write_json(manifest_path, manifest)

        prepare(
            argparse.Namespace(
                project_dir=project_dir,
                shot_id="S001",
                anchor_type="index_finger_mid",
                anchor_bbox=[100, 500, 20, 60],
                selected_ratio=4.2,
                anchor_physical_cm=None,
                target_width_px=None,
                target_center=[400, 600],
                height_ratio=0.9,
                evidence="同景深接触食指中段标注框",
            )
        )
        prompt = project_dir / "review" / "image-generation-prompts" / "S001.txt"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(VALID_REPLACEMENT_PROMPT, encoding="utf-8")

        redirect_path = project_dir / "planning" / "execution_redirect.json"
        write_json(redirect_path, {"status": "redirected", "target_project": "/safe/current-project"})
        must_block(
            lambda: authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True)),
            "PROJECT_EXECUTION_REDIRECTED",
        )
        redirect_path.unlink()

        project = load_json(project_dir / "project.json")
        current_lock = dict(project["skill_release_lock"])
        project["skill_release_lock"]["bundle_release_id"] = "video-remix-1.0.5"
        write_json(project_dir / "project.json", project)
        must_block(
            lambda: authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True)),
            "LEGACY_PROJECT_GENERATION_BLOCKED",
        )
        project = load_json(project_dir / "project.json")
        project["skill_release_lock"] = current_lock
        write_json(project_dir / "project.json", project)

        must_block(
            lambda: authorize(authorization_args(project_dir, prompt, face, edits=["product"], atomic=False)),
            "ATOMIC_EDIT_SET_MISMATCH",
        )
        receipt = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True))
        assert receipt["atomic_identity_product"] is True
        roles = [item["role"] for item in receipt["required_image_inputs"]]
        assert roles[0] == "immutable_original_source"
        assert "geometry_only_do_not_render_overlay" in roles
        assert "identity_only" in roles
        assert "product_role_bound" in roles

        request_path = project_dir / "review" / "image-generation-requests" / f"S001-{receipt['request_id']}.authorization.json"
        topology_qa, instance_qa, _ = write_state_audits(project_dir, shot["product_state"], "S001-base")
        bad_output = project_dir / "review" / "candidates" / "S001-bad.png"
        bad_output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1200), (220, 210, 190)).save(bad_output)
        bad_surface_qa = write_surface_qa(project_dir, product, bad_output, [360, 566, 30, 68], "S001-bad")
        bad_qa = project_dir / "review" / "candidates" / "S001-bad.qa.json"
        write_json(
            bad_qa,
            {
                "identity": True,
                "product": False,
                "scale": False,
                "shape": True,
                "surface": True,
                "filling": True,
                "endpoint": True,
                "composition": True,
                "source_provenance": True,
                "evidence": {
                    "identity": "脸型五官与授权参考一致",
                    "product": "旧产品仍可见，替换失败",
                    "scale": "产品实测宽度只有30像素",
                    "shape": "完整轮廓保持连续圆弧且无直角",
                    "surface": "侧光可见细粉雾",
                    "filling": "小开口内馅连续",
                    "endpoint": "仅微露馅",
                    "composition": "人物与背景未漂移",
                    "source_provenance": "构图对应精确原始首帧",
                },
            },
        )
        rejected = record_result(
            argparse.Namespace(
                project_dir=project_dir,
                authorization=request_path,
                output=bad_output,
                qa=bad_qa,
                product_bbox=[360, 566, 30, 68],
                topology_qa=topology_qa,
                instance_qa=instance_qa,
                continuity_qa=None,
                surface_qa=bad_surface_qa,
                scale_relationship_qa=None,
            )
        )
        assert rejected["status"] == "rejected_diagnostic"
        assert rejected["retry_instruction"] == "return_to_exact_original_source"
        assert rejected["partial_candidate_reusable"] is False
        current_shot = load_json(manifest_path)["shots"][0]
        assert not (current_shot.get("asset_links") or {}).get("candidate_generation_first_frame")
        must_block(
            lambda: authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True)),
            "BLIND_RETRY_BLOCKED",
        )

        prompt.write_text(VALID_REPLACEMENT_PROMPT + "尺寸导引图已绑定，扩大到导引框并同轮通过。", encoding="utf-8")
        receipt2 = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True))
        request_path2 = project_dir / "review" / "image-generation-requests" / f"S001-{receipt2['request_id']}.authorization.json"
        good_output = project_dir / "review" / "candidates" / "S001-good.png"
        Image.new("RGB", (800, 1200), (225, 215, 195)).save(good_output)
        good_surface_qa = write_surface_qa(project_dir, product, good_output, [358, 562, 84, 76], "S001-good")
        good_scale_relationship_qa = write_scale_relationship_qa(project_dir, good_output, [358, 562, 84, 76], "S001-good")
        good_qa = project_dir / "review" / "candidates" / "S001-good.qa.json"
        qa_keys = ("identity", "product", "scale", "shape", "state_topology", "instance_count", "instance_variation", "surface", "filling", "endpoint", "composition", "source_provenance")
        write_json(
            good_qa,
            {
                **{key: True for key in qa_keys},
                "evidence": {key: f"{key} 已按原尺寸画面逐项核对" for key in qa_keys},
            },
        )
        approved = record_result(
            argparse.Namespace(
                project_dir=project_dir,
                authorization=request_path2,
                output=good_output,
                qa=good_qa,
                product_bbox=[358, 562, 84, 76],
                topology_qa=topology_qa,
                instance_qa=instance_qa,
                continuity_qa=None,
                surface_qa=good_surface_qa,
                scale_relationship_qa=good_scale_relationship_qa,
            )
        )
        assert approved["status"] == "approved_candidate", approved
        approval_path = project_dir / "review" / "gallery" / "S001-good.approval.json"
        write_json(
            approval_path,
            {
                "status": "user_approved",
                "display_receipt_id": "gallery-S001-good",
                "approved_at": "2026-08-26T09:00:00+08:00",
                "request_id": receipt2["request_id"],
                "asset_sha256": hashlib.sha256(good_output.read_bytes()).hexdigest(),
            },
        )
        promotion = promote_user_approved_result(argparse.Namespace(
            project_dir=project_dir,
            authorization=request_path2,
            approval=approval_path,
        ))
        assert promotion["status"] == "promoted_to_canonical"
        final_shot = load_json(manifest_path)["shots"][0]
        assert final_shot["asset_links"]["approved_generation_first_frame"] == final_shot["asset_links"]["candidate_generation_first_frame"]
        assert final_shot["asset_links"]["user_approval"]["status"] == "user_approved"
        assert not validate_approved_result_binding(project_dir, project, final_shot)

        # Packaging can remain square, but all four product instances must keep the same rounded geometry identity.
        packaged_manifest = load_json(manifest_path)
        packaged_shot = packaged_manifest["shots"][0]
        packaged_shot["product_state"]["packaging"] = "visible_open_box"
        packaged_shot["product_state"]["package_content_lock"] = {
            "geometry_identity_id": "DF2-ROUND-7CM-001",
            "container_geometry_independent": True,
            "tray_cell_role": "support_and_occlusion_only",
            "per_visible_instance_shape_qa": True,
            "package_capacity_count": 4,
            "accounted_product_count": 4,
            "instance_ids": ["DF-PKG-01", "DF-PKG-02", "DF-PKG-03", "DF-PKG-04"],
        }
        packaged_shot["product_state"]["count"] = 4
        packaged_shot["product_state"]["instance_lock"].update(
            {
                "source_product_count": 4,
                "target_product_count": 4,
                "instance_ids": ["DF-PKG-01", "DF-PKG-02", "DF-PKG-03", "DF-PKG-04"],
                "shape_variant_ids": ["DF-VAR-01", "DF-VAR-02", "DF-VAR-03", "DF-VAR-04"],
            }
        )
        packaged_shot["product_state"]["arrangement_lock"] = {
            "layout_id": "LAYOUT-PKG-S001",
            "previous_layout_id": None,
            "container_id": "PACKAGE-01",
            "inventory_stage_id": "INV-PKG-01",
            "instance_ids": ["DF-PKG-01", "DF-PKG-02", "DF-PKG-03", "DF-PKG-04"],
            "natural_irregularity_required": True,
            "perfect_grid": False,
            "equal_spacing": False,
            "uniform_orientation": False,
            "relative_topology_preserved": True,
            "event": {"type": "initial"},
        }
        retail_front = next(item for item in product["reference_assets"] if item["id"] == "DF2-PACK-RETAIL-FRONT-01")
        packaged_shot["product_state"]["packaging_lock"] = {
            "visible": True,
            "package_levels": ["retail_outer_box"],
            "visible_faces_by_level": {"retail_outer_box": ["front"]},
            "reference_asset_ids_by_level": {"retail_outer_box": {"front": "DF2-PACK-RETAIL-FRONT-01"}},
            "invented_packaging_allowed": False,
            "artwork_mode": "approved_master_projection_only",
        }
        packaged_shot["product_state"]["reference_roles"].append({
            "asset_id": retail_front["id"],
            "role": retail_front["role"],
            "allowed_inheritance": retail_front["allowed_inheritance"],
            "forbidden_inheritance": retail_front["forbidden_inheritance"],
        })
        packaged_shot["asset_links"]["product_references"].append(retail_front["target_path"])
        write_json(manifest_path, packaged_manifest)
        package_topology_qa, package_instance_qa, package_continuity_qa = write_state_audits(project_dir, packaged_shot["product_state"], "S001-package", packaging=True)
        prompt.write_text(VALID_PACKAGING_PROMPT + "盒体可方，盒内四颗逐颗保持DF2-ROUND-7CM-001圆润轮廓。", encoding="utf-8")
        package_receipt = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True))
        assert package_receipt["packaging_reference_binding"][0]["asset_id"] == "DF2-PACK-RETAIL-FRONT-01"
        package_request = project_dir / "review" / "image-generation-requests" / f"S001-{package_receipt['request_id']}.authorization.json"
        package_output = project_dir / "review" / "candidates" / "S001-package-square.png"
        Image.new("RGB", (800, 1200), (228, 216, 198)).save(package_output)
        package_surface_qa = write_surface_qa(project_dir, product, package_output, [362, 566, 75, 68], "S001-package-square")
        package_qa = project_dir / "review" / "candidates" / "S001-package.qa.json"
        package_keys = (*qa_keys, "layout_continuity", "inventory_transition", "package_product_geometry")
        write_json(package_qa, {**{key: True for key in package_keys}, "evidence": {key: f"{key}逐项核对" for key in package_keys}})
        bad_shape_qa = project_dir / "review" / "candidates" / "S001-package-square.shape.json"
        write_json(
            bad_shape_qa,
            {
                "schema_version": "durian-daifuku-shape-qa-v1.0",
                "geometry_identity_id": "DF2-ROUND-7CM-001",
                "container_shape_inheritance": False,
                "cross_context_match": True,
                "accounted_product_count": 4,
                "instances": [
                    {
                        "instance_id": f"DF-PKG-{index:02d}",
                        "visibility": "visible",
                        "geometry_identity_id": "DF2-ROUND-7CM-001",
                        "silhouette_family": "rounded_slightly_oblate",
                        "bbox_aspect_ratio": 1.0,
                        "straight_edge_fraction": 0.6 if index == 1 else 0.08,
                        "right_angle_corner_count": 4 if index == 1 else 0,
                        "pass": index != 1,
                        "evidence": "逐颗轮廓检测",
                    }
                    for index in range(1, 5)
                ],
            },
        )
        package_rejected = record_result(
            argparse.Namespace(
                project_dir=project_dir,
                authorization=package_request,
                output=package_output,
                qa=package_qa,
                product_bbox=[362, 566, 75, 68],
                shape_qa=bad_shape_qa,
                topology_qa=package_topology_qa,
                instance_qa=package_instance_qa,
                continuity_qa=package_continuity_qa,
                surface_qa=package_surface_qa,
                scale_relationship_qa=None,
            )
        )
        assert package_rejected["status"] == "rejected_diagnostic"
        assert "packaged_instance_shape_geometry" in package_rejected["failed_checks"]

        prompt.write_text(VALID_PACKAGING_PROMPT + "修正盒内第一颗，不继承方格直边，四颗全部逐颗复核为圆润略扁。", encoding="utf-8")
        package_receipt2 = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True))
        package_request2 = project_dir / "review" / "image-generation-requests" / f"S001-{package_receipt2['request_id']}.authorization.json"
        package_output2 = project_dir / "review" / "candidates" / "S001-package-rounded.png"
        Image.new("RGB", (800, 1200), (229, 217, 199)).save(package_output2)
        package_surface_qa2 = write_surface_qa(project_dir, product, package_output2, [358, 562, 84, 76], "S001-package-rounded")
        package_scale_relationship_qa2 = write_scale_relationship_qa(project_dir, package_output2, [358, 562, 84, 76], "S001-package-rounded")
        good_shape_qa = project_dir / "review" / "candidates" / "S001-package-rounded.shape.json"
        shape_payload = load_json(bad_shape_qa)
        for item in shape_payload["instances"]:
            item["straight_edge_fraction"] = 0.08
            item["right_angle_corner_count"] = 0
            item["pass"] = True
        write_json(good_shape_qa, shape_payload)
        package_approved = record_result(
            argparse.Namespace(
                project_dir=project_dir,
                authorization=package_request2,
                output=package_output2,
                qa=package_qa,
                product_bbox=[358, 562, 84, 76],
                shape_qa=good_shape_qa,
                topology_qa=package_topology_qa,
                instance_qa=package_instance_qa,
                continuity_qa=package_continuity_qa,
                surface_qa=package_surface_qa2,
                scale_relationship_qa=package_scale_relationship_qa2,
            )
        )
        assert package_approved["status"] == "approved_candidate", package_approved
        assert package_approved["shape_qa"]["accounted_product_count"] == 4
        assert package_approved["shape_qa"]["audit_valid"] is True

        good_output.write_bytes(good_output.read_bytes() + b"tampered")
        codes = {code for code, _ in validate_approved_result_binding(project_dir, project, final_shot)}
        assert "APPROVED_FRAME_RESULT_HASH_MISMATCH" in codes

        # A delivery-unit exact first frame can truthfully contain no product even though
        # the video terminal state later contains replacement products. This must not
        # silently bypass source evidence or leak product references/scale guides.
        absent_source = project_dir / "source" / "frames" / "SRC-CARRIER.png"
        Image.new("RGB", (800, 1200), (210, 205, 198)).save(absent_source)
        absent_shot = copy.deepcopy(shot)
        absent_shot["source_units"] = [{
            "source_shot_id": "SRC001",
            "source_first_frame": "source/frames/SRC-CARRIER.png",
            "packaging_evidence": {"visible": False, "evidence": "透明运输保护外载体，无产品包装面"},
            "exact_first_frame_generation_contract": {
                "product_visibility": "absent",
                "product_edit_required": False,
                "product_reference_inputs_required": False,
                "visible_target_product_count": 0,
                "pixel_plan_applicability": "not_applicable_product_absent",
                "source_observation": {"product_visible": False, "visible_product_count": 0},
            },
        }]
        absent_shot["inserted_units"] = []
        absent_shot["product_state"]["count"] = 12  # later video terminal/action state remains intact
        write_json(manifest_path, {"shots": [absent_shot]})
        prompt.write_text(VALID_ABSENT_PROMPT + "精确首帧保持透明运输保护外载体，首帧不提前出现产品或零售盒。", encoding="utf-8")
        absent_receipt = authorize(authorization_args(project_dir, prompt, face, edits=["identity"], atomic=False, unit_id="SRC001"))
        assert absent_receipt["unit_id"] == "SRC001"
        assert absent_receipt["requested_edits"] == ["identity"]
        assert absent_receipt["product_references"] == []
        assert absent_receipt["scale_guides"] == []
        assert absent_receipt["exact_first_frame_generation_contract"]["product_visibility"] == "absent"
        assert absent_shot["product_state"]["count"] == 12

        absent_request = project_dir / "review" / "image-generation-requests" / f"S001-SRC001-{absent_receipt['request_id']}.authorization.json"
        absent_output = project_dir / "review" / "candidates" / "S001-SRC001-carrier.png"
        Image.new("RGB", (800, 1200), (212, 207, 200)).save(absent_output)
        absent_qa = project_dir / "review" / "candidates" / "S001-SRC001-carrier.qa.json"
        absent_keys = ("identity", "composition", "source_provenance")
        write_json(absent_qa, {**{key: True for key in absent_keys}, "evidence": {key: f"{key}逐项核对" for key in absent_keys}})
        absent_result = record_result(argparse.Namespace(
            project_dir=project_dir,
            authorization=absent_request,
            output=absent_output,
            qa=absent_qa,
            product_bbox=None,
            shape_qa=None,
            topology_qa=None,
            instance_qa=None,
            continuity_qa=None,
            surface_qa=None,
            scale_relationship_qa=None,
        ))
        assert absent_result["status"] == "approved_candidate", absent_result
        assert absent_result["exact_first_frame_product_visibility"] == "absent"
        assert absent_result["joint_qa"]["product"] == "not_applicable"

        neutralized = load_json(manifest_path)
        neutral_unit = neutralized["shots"][0]["source_units"][0]
        neutral_unit["packaging_evidence"] = {"visible": True, "evidence": "原帧可见旧产品印刷袋"}
        neutral_contract = neutral_unit["exact_first_frame_generation_contract"]
        neutral_contract["source_observation"] = {"product_visible": True, "visible_product_count": 1}
        neutral_contract["product_edit_required"] = True
        neutral_contract["source_product_action"] = "neutralize_to_non_product_carrier"
        neutral_state = neutralized["shots"][0]["product_state"]
        neutral_state["packaging"] = "retail_outer_box_later_video_phase"
        neutral_state["packaging_lock"] = {
            "visible": True,
            "package_levels": ["retail_outer_box"],
            "visible_faces_by_level": {"retail_outer_box": ["front"]},
            "reference_asset_ids_by_level": {"retail_outer_box": {"front": "DF2-PACK-RETAIL-FRONT-01"}},
            "invented_packaging_allowed": False,
            "artwork_mode": "approved_master_projection_only",
        }
        neutral_state["count"] = 12
        neutral_state["instance_lock"].update({
            "source_product_count": 12,
            "target_product_count": 12,
            "instance_ids": [f"DF-LATER-{index:02d}" for index in range(1, 13)],
            "shape_variant_ids": [f"DF-LATER-VAR-{index:02d}" for index in range(1, 13)],
        })
        neutral_state["arrangement_lock"] = {
            "layout_id": "LAYOUT-LATER-VIDEO-PHASE",
            "container_id": "THREE-RETAIL-BOXES-LATER",
            "instance_ids": [f"DF-LATER-{index:02d}" for index in range(1, 13)],
        }
        write_json(manifest_path, neutralized)
        prompt.write_text(VALID_ABSENT_PROMPT + "同时换人物并移除旧产品印刷袋，将其变为无品牌透明保护载体；目标首帧仍不出现大福或零售盒。", encoding="utf-8")
        neutral_receipt = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True, unit_id="SRC001"))
        assert neutral_receipt["requested_edits"] == ["identity", "product"]
        assert neutral_receipt["product_references"] == []
        assert neutral_receipt["scale_guides"] == []
        assert neutral_receipt["packaging_reference_binding"] == []

        neutral_request = project_dir / "review" / "image-generation-requests" / f"S001-SRC001-{neutral_receipt['request_id']}.authorization.json"
        neutral_output = project_dir / "review" / "candidates" / "S001-SRC001-neutralized-carrier.png"
        Image.new("RGB", (800, 1200), (214, 209, 201)).save(neutral_output)
        neutral_qa = project_dir / "review" / "candidates" / "S001-SRC001-neutralized-carrier.qa.json"
        neutral_keys = ("identity", "product", "composition", "source_provenance")
        write_json(neutral_qa, {
            **{key: True for key in neutral_keys},
            "evidence": {
                "identity": "人物身份与授权参考一致。",
                "product": "原片旧产品印刷、品牌文字和产品示意已清零；目标首帧大福0、零售盒0，仅保留无品牌透明保护载体。",
                "composition": "原手部抓握、遮挡、透明载体褶皱、场景与光影保持。",
                "source_provenance": "仅从精确原始首帧与授权人物身份参考生成，未加载目标产品、包装母版或尺度导引。",
            },
        })
        neutral_result = record_result(argparse.Namespace(
            project_dir=project_dir,
            authorization=neutral_request,
            output=neutral_output,
            qa=neutral_qa,
            product_bbox=None,
            shape_qa=None,
            topology_qa=None,
            instance_qa=None,
            continuity_qa=None,
            surface_qa=None,
            scale_relationship_qa=None,
        ))
        assert neutral_result["status"] == "approved_candidate", neutral_result
        assert neutral_result["joint_qa"]["product"] is True
        assert neutral_result["joint_qa"]["package_product_geometry"] == "not_applicable"
        assert neutral_result["joint_qa"]["layout_continuity"] == "not_applicable"
        assert neutral_result["joint_qa"]["inventory_transition"] == "not_applicable"
        assert not any(key in neutral_result["failed_checks"] for key in ("package_product_geometry", "layout_continuity", "inventory_transition"))

        broken = load_json(manifest_path)
        broken_unit = broken["shots"][0]["source_units"][0]
        broken_unit["exact_first_frame_generation_contract"]["product_edit_required"] = False
        write_json(manifest_path, broken)
        prompt.write_text("改变合同以触发证据冲突。\n", encoding="utf-8")
        must_block(
            lambda: authorize(authorization_args(project_dir, prompt, face, edits=["identity"], atomic=False, unit_id="SRC001")),
            "EXACT_FIRST_FRAME_PRODUCT_EDIT_MODE_MISMATCH",
        )

    print(json.dumps({"status": "ok", "contract": "atomic-image-generation-gate"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
