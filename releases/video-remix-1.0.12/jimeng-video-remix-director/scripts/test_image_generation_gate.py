#!/usr/bin/env python3
"""Regression tests for legacy blocking, atomic edits and original-source retries."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from image_generation_gate import GateError, authorize, record_result, validate_approved_result_binding  # noqa: E402
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


def authorization_args(project_dir: Path, prompt: Path, face: Path, *, edits: list[str], atomic: bool) -> argparse.Namespace:
    return argparse.Namespace(
        project_dir=project_dir,
        shot_id="S001",
        prompt_file=prompt,
        edit=edits,
        atomic=atomic,
        face_reference=[str(face)],
    )


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


def main() -> int:
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
                selected_ratio=3.75,
                anchor_physical_cm=None,
                target_width_px=None,
                target_center=[400, 600],
                height_ratio=0.9,
                evidence="同景深接触食指中段标注框",
            )
        )
        prompt = project_dir / "review" / "image-generation-prompts" / "S001.txt"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("从原图一次性同时替换人物身份与榴莲大福；严格遵循尺寸导引图。\n", encoding="utf-8")

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

        prompt.write_text("从同一原图一次性同时替换人物身份与榴莲大福；扩大到导引框，粉雾皮面与连续果泥同轮通过。\n", encoding="utf-8")
        receipt2 = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True))
        request_path2 = project_dir / "review" / "image-generation-requests" / f"S001-{receipt2['request_id']}.authorization.json"
        good_output = project_dir / "review" / "candidates" / "S001-good.png"
        Image.new("RGB", (800, 1200), (225, 215, 195)).save(good_output)
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
                product_bbox=[362, 566, 75, 68],
                topology_qa=topology_qa,
                instance_qa=instance_qa,
                continuity_qa=None,
            )
        )
        assert approved["status"] == "approved_candidate", approved
        manifest = load_json(manifest_path)
        final_shot = manifest["shots"][0]
        final_shot["asset_links"]["approved_generation_first_frame"] = final_shot["asset_links"]["candidate_generation_first_frame"]
        write_json(manifest_path, manifest)
        final_shot = load_json(manifest_path)["shots"][0]
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
        write_json(manifest_path, packaged_manifest)
        package_topology_qa, package_instance_qa, package_continuity_qa = write_state_audits(project_dir, packaged_shot["product_state"], "S001-package", packaging=True)
        prompt.write_text("从同一原图原子替换人物与产品；盒体可方，盒内四颗逐颗保持DF2-ROUND-7CM-001圆润轮廓。\n", encoding="utf-8")
        package_receipt = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True))
        package_request = project_dir / "review" / "image-generation-requests" / f"S001-{package_receipt['request_id']}.authorization.json"
        package_output = project_dir / "review" / "candidates" / "S001-package-square.png"
        Image.new("RGB", (800, 1200), (228, 216, 198)).save(package_output)
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
            )
        )
        assert package_rejected["status"] == "rejected_diagnostic"
        assert "packaged_instance_shape_geometry" in package_rejected["failed_checks"]

        prompt.write_text("仍从同一原图原子替换；修正盒内第一颗，不继承方格直边，四颗全部逐颗复核为圆润略扁。\n", encoding="utf-8")
        package_receipt2 = authorize(authorization_args(project_dir, prompt, face, edits=["identity", "product"], atomic=True))
        package_request2 = project_dir / "review" / "image-generation-requests" / f"S001-{package_receipt2['request_id']}.authorization.json"
        package_output2 = project_dir / "review" / "candidates" / "S001-package-rounded.png"
        Image.new("RGB", (800, 1200), (229, 217, 199)).save(package_output2)
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
                product_bbox=[362, 566, 75, 68],
                shape_qa=good_shape_qa,
                topology_qa=package_topology_qa,
                instance_qa=package_instance_qa,
                continuity_qa=package_continuity_qa,
            )
        )
        assert package_approved["status"] == "approved_candidate", package_approved
        assert package_approved["shape_qa"]["accounted_product_count"] == 4
        assert package_approved["shape_qa"]["audit_valid"] is True

        good_output.write_bytes(good_output.read_bytes() + b"tampered")
        codes = {code for code, _ in validate_approved_result_binding(project_dir, project, final_shot)}
        assert "APPROVED_FRAME_RESULT_HASH_MISMATCH" in codes

    print(json.dumps({"status": "ok", "contract": "atomic-image-generation-gate"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
