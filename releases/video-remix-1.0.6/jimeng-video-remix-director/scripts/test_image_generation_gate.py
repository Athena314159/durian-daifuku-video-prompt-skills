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
                "surface": True,
                "filling": True,
                "endpoint": True,
                "composition": True,
                "source_provenance": True,
                "evidence": {
                    "identity": "脸型五官与授权参考一致",
                    "product": "旧产品仍可见，替换失败",
                    "scale": "产品实测宽度只有30像素",
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
        qa_keys = ("identity", "product", "scale", "surface", "filling", "endpoint", "composition", "source_provenance")
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
            )
        )
        assert approved["status"] == "approved_candidate", approved
        manifest = load_json(manifest_path)
        final_shot = manifest["shots"][0]
        final_shot["asset_links"]["approved_generation_first_frame"] = final_shot["asset_links"]["candidate_generation_first_frame"]
        write_json(manifest_path, manifest)
        final_shot = load_json(manifest_path)["shots"][0]
        assert not validate_approved_result_binding(project_dir, project, final_shot)

        good_output.write_bytes(good_output.read_bytes() + b"tampered")
        codes = {code for code, _ in validate_approved_result_binding(project_dir, project, final_shot)}
        assert "APPROVED_FRAME_RESULT_HASH_MISMATCH" in codes

    print(json.dumps({"status": "ok", "contract": "atomic-image-generation-gate"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
