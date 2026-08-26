#!/usr/bin/env python3
"""Validate cross-project asset inventory and frame-reuse decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image


ASSET_TYPES = {
    "avatar_reference",
    "face_approved_frame",
    "product_reference",
    "product_approved_frame",
    "source_frame",
    "beauty_or_action_candidate",
    "approved_frame",
    "word_extracted_frame",
    "generated_result",
}
LIBRARY_LAYERS = {"avatar_identity", "product_packaging", "scene_shot", "delivery"}
DECISIONS = {"reuse", "new_generation", "omit"}
GENERATION_REASONS = {
    "no_candidate_asset",
    "file_missing_or_corrupt",
    "rights_not_cleared",
    "unsafe_aspect_ratio_transform",
    "pixel_qa_failed",
    "identity_mismatch",
    "product_or_state_mismatch",
    "scene_or_action_mismatch",
    "source_subtitle_or_watermark",
}
DIRECT_DELIVERY_TYPES = {
    "face_approved_frame",
    "product_approved_frame",
    "approved_frame",
    "word_extracted_frame",
    "generated_result",
}
NO_GENERATION_MODE = "no_generation_prompt_docx_alignment"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(raw: str, project_root: Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else project_root / path


def audit_no_generation_plan(plan: dict[str, Any], project: dict[str, Any], project_root: Path, stage: str) -> int:
    """Audit formal no-generation references without inventing gallery approval."""
    errors: list[str] = []
    binding = plan.get("contract_binding") if isinstance(plan.get("contract_binding"), dict) else {}
    lock = project.get("skill_release_lock") if isinstance(project.get("skill_release_lock"), dict) else {}
    expected_binding = {"bundle_release_id": lock.get("bundle_release_id"), "prompt_authoring_contract": lock.get("prompt_authoring_contract"), "product_profile": project.get("product_profile")}
    if any(binding.get(key) != value for key, value in expected_binding.items()):
        errors.append("no-generation asset_reuse_plan.contract_binding 与当前项目不一致")
    contract_path = project_root / "planning" / "no_generation_prompt_docx_alignment_contract.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        contract = {}
        errors.append("缺少或无法解析 no-generation DOCX 合同")
    if contract.get("schema_version") != "no-generation-prompt-docx-alignment-v1.0":
        errors.append("no-generation DOCX 合同 schema 不匹配")
    if contract.get("approved_target_frame_count") != 0:
        errors.append("no-generation 合同批准目标帧数量必须为0")
    refs = contract.get("references") if isinstance(contract.get("references"), list) else []
    inventory = plan.get("inventory") if isinstance(plan.get("inventory"), list) else []
    decisions = plan.get("shot_decisions") if isinstance(plan.get("shot_decisions"), list) else []
    expected_owners = [str(item.get("owner_unit_id")) for item in refs if isinstance(item, dict)]
    actual_owners = [str(item.get("owner_unit_id")) for item in inventory if isinstance(item, dict)]
    if actual_owners != expected_owners:
        errors.append("no-generation inventory owner 顺序必须与合同参考顺序一致")
    allowed_roles = {"legacy_composition_reference", "legacy_rejected_example", "exact_source_reuse"}
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            errors.append(f"no-generation inventory[{index}] 必须为对象")
            continue
        owner = item.get("owner_unit_id") or f"inventory[{index}]"
        if item.get("asset_role") not in allowed_roles:
            errors.append(f"{owner} asset_role 不属于 no-generation 允许角色")
        if item.get("approval_status") != "audit_only" or item.get("approved") is not False:
            errors.append(f"{owner} 必须保持 audit_only 且 approved=false")
        raw_path = item.get("path")
        path = resolve_path(raw_path, project_root) if isinstance(raw_path, str) else None
        if path is None or not path.is_file():
            errors.append(f"{owner} 参考文件不可访问")
            continue
        actual = sha256(path)
        if item.get("sha256") != actual:
            errors.append(f"{owner} SHA-256 与文件不一致")
        try:
            with Image.open(path) as image:
                width, height = image.size
            if width * 16 != height * 9:
                errors.append(f"{owner} 不是独立9:16图")
        except Exception as exc:
            errors.append(f"{owner} 不是可读图片：{exc}")
    for decision in decisions:
        if not isinstance(decision, dict):
            errors.append("no-generation shot_decision 必须为对象")
            continue
        if decision.get("decision") != "omit" or decision.get("selected_asset_ids"):
            errors.append(f"{decision.get('shot_id')} no-generation 必须 omit 且 selected_asset_ids 为空")
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    if summary.get("expected_word_image_count") != len(refs):
        errors.append("no-generation expected_word_image_count 必须等于合同 reference_count")
    if summary.get("approved_target_frame_count") != 0:
        errors.append("no-generation summary.approved_target_frame_count 必须为0")
    if errors:
        print(f"资产复用审核阻断：{len(errors)}项")
        for item in errors:
            print(f"- {item}")
        return 1
    print(f"no-generation 资产复用审核通过：{len(refs)}张合同参考图，0张批准目标帧；stage={stage}，不要求 gallery user approval。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, help="planning/asset_reuse_plan.json")
    parser.add_argument(
        "--stage",
        choices=("pre-generation", "pre-word"),
        default="pre-generation",
        help="Pre-word additionally requires every delivery image to exist.",
    )
    args = parser.parse_args()

    plan_path = Path(args.plan).expanduser().resolve()
    if not plan_path.is_file():
        print(f"资产复用审核阻断：计划文件不可访问：{plan_path}")
        return 2

    try:
        plan: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"资产复用审核阻断：计划文件无法解析：{exc}")
        return 2

    errors: list[str] = []
    warnings: list[str] = []
    project_root = plan_path.parent.parent
    project_path = project_root / "project.json"
    if not project_path.is_file():
        errors.append("缺少 project.json，无法验证版本与产品来源")
        project: dict[str, Any] = {}
    else:
        project = json.loads(project_path.read_text(encoding="utf-8"))
    if project.get("document_delivery_mode") == NO_GENERATION_MODE:
        return audit_no_generation_plan(plan, project, project_root, args.stage)
    release_lock = project.get("skill_release_lock") if isinstance(project.get("skill_release_lock"), dict) else {}
    binding = plan.get("contract_binding") if isinstance(plan.get("contract_binding"), dict) else {}
    expected_binding = {
        "bundle_release_id": release_lock.get("bundle_release_id"),
        "prompt_authoring_contract": release_lock.get("prompt_authoring_contract"),
        "product_profile": project.get("product_profile"),
    }
    if any(binding.get(key) != value for key, value in expected_binding.items()):
        errors.append("asset_reuse_plan.contract_binding 与当前项目版本/产品合同不一致；旧资产计划必须重建")
    inventory = plan.get("inventory")
    decisions = plan.get("shot_decisions")
    summary = plan.get("summary")
    libraries = plan.get("libraries")

    if plan.get("status") != "reviewed":
        errors.append("status 必须为 reviewed")
    if not isinstance(libraries, dict):
        errors.append("缺少 libraries 层级映射")
    else:
        for key in ("avatar_library", "product_library", "product_bible", "knowledge_index"):
            if key not in libraries:
                errors.append(f"libraries 缺少 {key}")
    if not isinstance(inventory, list):
        errors.append("inventory 必须为数组")
        inventory = []
    if not isinstance(decisions, list) or not decisions:
        errors.append("shot_decisions 必须为非空数组")
        decisions = []
    if not isinstance(summary, dict):
        errors.append("summary 必须为对象")
        summary = {}

    assets: dict[str, dict[str, Any]] = {}
    for index, asset in enumerate(inventory):
        base = f"inventory[{index}]"
        if not isinstance(asset, dict):
            errors.append(f"{base} 必须为对象")
            continue
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            errors.append(f"{base} 缺少 asset_id")
            continue
        if asset_id in assets:
            errors.append(f"重复 asset_id：{asset_id}")
            continue
        assets[asset_id] = asset
        if asset.get("asset_type") not in ASSET_TYPES:
            errors.append(f"{asset_id} 的 asset_type 非法")
        if asset.get("library_layer") not in LIBRARY_LAYERS:
            errors.append(f"{asset_id} 的 library_layer 非法")

    selected_records: list[tuple[str, str, dict[str, Any]]] = []
    reused_count = 0
    planned_generation_count = 0
    seen_shots: set[str] = set()

    for index, decision in enumerate(decisions):
        base = f"shot_decisions[{index}]"
        if not isinstance(decision, dict):
            errors.append(f"{base} 必须为对象")
            continue
        shot_id = decision.get("shot_id")
        if not isinstance(shot_id, str) or not shot_id:
            errors.append(f"{base} 缺少 shot_id")
            continue
        if shot_id in seen_shots:
            errors.append(f"重复镜头决定：{shot_id}")
        seen_shots.add(shot_id)
        mode = decision.get("decision")
        if mode not in DECISIONS:
            errors.append(f"{shot_id} 的 decision 非法")
            continue

        candidate_ids = decision.get("candidate_asset_ids", [])
        selected_ids = decision.get("selected_asset_ids", [])
        if not isinstance(candidate_ids, list) or not all(isinstance(x, str) for x in candidate_ids):
            errors.append(f"{shot_id} 的 candidate_asset_ids 必须为字符串数组")
            candidate_ids = []
        if not isinstance(selected_ids, list) or not all(isinstance(x, str) for x in selected_ids):
            errors.append(f"{shot_id} 的 selected_asset_ids 必须为字符串数组")
            selected_ids = []
        for asset_id in candidate_ids + selected_ids:
            if asset_id not in assets:
                errors.append(f"{shot_id} 引用了不存在的资产 {asset_id}")

        required_avatars = decision.get("required_avatar_ids", [])
        required_products = decision.get("required_product_ids", [])
        if required_avatars and decision.get("identity_review") != "matched_and_authorized":
            errors.append(f"{shot_id} 启用人物/换脸资产但 identity_review 未通过授权审核")
        if required_products and decision.get("product_review") != "matched":
            errors.append(f"{shot_id} 启用产品资产但 product_review 未通过")
        if required_avatars and required_products:
            if not decision.get("cross_layer_pixel_protection"):
                errors.append(f"{shot_id} 同时换脸和换产品但缺少 cross_layer_pixel_protection")
            if decision.get("atomic_identity_product_required") is not True:
                errors.append(f"{shot_id} 同时换脸和换产品必须设置 atomic_identity_product_required=true")
            if decision.get("retry_origin_policy") != "exact_original_source_only":
                errors.append(f"{shot_id} 重试必须返回精确原始 source_first_frame")
            if decision.get("partial_candidate_policy") != "diagnostic_only_never_reuse":
                errors.append(f"{shot_id} 半成品候选必须仅诊断且禁止复用")

        if mode == "reuse":
            if not candidate_ids or not selected_ids:
                errors.append(f"{shot_id} 选择 reuse 时必须列出候选和选中资产")
            reused_count += len(selected_ids)
        elif mode == "new_generation":
            count = decision.get("planned_generation_count", 1)
            if not isinstance(count, int) or count < 1:
                errors.append(f"{shot_id} 的 planned_generation_count 必须为正整数")
                count = 0
            planned_generation_count += count
            reason = decision.get("generation_reason")
            if reason not in GENERATION_REASONS:
                errors.append(f"{shot_id} 补生缺少合法 generation_reason")
            rejection_reasons = decision.get("candidate_rejection_reasons")
            if reason != "no_candidate_asset" and (not isinstance(rejection_reasons, list) or not rejection_reasons):
                errors.append(f"{shot_id} 补生前必须记录候选拒收原因")
            if args.stage == "pre-word" and len(selected_ids) != count:
                errors.append(f"{shot_id} 进入 Word 前选中补生帧数必须等于 planned_generation_count")

        for asset_id in selected_ids:
            asset = assets.get(asset_id)
            if asset:
                selected_records.append((shot_id, asset_id, asset))

    selected_hashes: dict[str, tuple[str, str]] = {}
    for shot_id, asset_id, asset in selected_records:
        if asset.get("origin_bundle_release_id") != release_lock.get("bundle_release_id"):
            errors.append(f"{shot_id}/{asset_id} 来源 release 与当前项目不一致；旧版本批准图不得直接复用")
        if asset.get("product_ids") and asset.get("origin_product_profile") != project.get("product_profile"):
            errors.append(f"{shot_id}/{asset_id} 来源产品合同与当前项目不一致")
        if asset.get("asset_type") not in DIRECT_DELIVERY_TYPES:
            errors.append(f"{shot_id}/{asset_id} 是参考或候选资产，未提升为可交付批准帧")
        allowed_approvals = {"user_approved"} if args.stage == "pre-word" else {"approved", "user_approved"}
        if asset.get("approval_status") not in allowed_approvals:
            errors.append(f"{shot_id}/{asset_id} approval_status 必须属于 {sorted(allowed_approvals)}")
        if asset.get("rights_status") not in {"cleared", "not_applicable"}:
            errors.append(f"{shot_id}/{asset_id} 权利状态未清")
        if asset.get("has_source_subtitles") is not False:
            errors.append(f"{shot_id}/{asset_id} 未确认无源字幕")
        if asset.get("has_watermark") is not False:
            errors.append(f"{shot_id}/{asset_id} 未确认无水印")
        if asset.get("is_composite_or_contact_sheet") is not False:
            errors.append(f"{shot_id}/{asset_id} 是拼图/总览或未明确排除")

        raw_path = asset.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{shot_id}/{asset_id} 缺少 path")
            continue
        path = resolve_path(raw_path, project_root)
        if not path.is_file():
            errors.append(f"{shot_id}/{asset_id} 文件不可访问：{path}")
            continue
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Exception as exc:  # Pillow reports precise decoder failures.
            errors.append(f"{shot_id}/{asset_id} 不是可读图片：{exc}")
            continue
        if width * 16 != height * 9:
            errors.append(f"{shot_id}/{asset_id} 非独立9:16：{width}×{height}")
        if asset.get("width") != width or asset.get("height") != height:
            errors.append(f"{shot_id}/{asset_id} 记录尺寸与文件不一致")
        actual_hash = sha256(path)
        if asset.get("sha256") != actual_hash:
            errors.append(f"{shot_id}/{asset_id} SHA-256 与文件不一致")
        if actual_hash in selected_hashes:
            previous_shot, previous_asset = selected_hashes[actual_hash]
            errors.append(
                f"{shot_id}/{asset_id} 与 {previous_shot}/{previous_asset} 内容重复；"
                "最终 Word 的每个 SRC/ADD 必须使用内容唯一的批准图，不接受重复图豁免"
            )
        else:
            selected_hashes[actual_hash] = (shot_id, asset_id)

        if args.stage == "pre-word":
            user_approval = asset.get("user_approval") if isinstance(asset.get("user_approval"), dict) else {}
            if user_approval.get("status") != "user_approved":
                errors.append(f"{shot_id}/{asset_id} 缺少用户批准状态")
            if user_approval.get("asset_sha256") != actual_hash:
                errors.append(f"{shot_id}/{asset_id} 用户批准回执未绑定当前图片 SHA-256")
            for field in ("display_receipt_id", "approved_at"):
                if not isinstance(user_approval.get(field), str) or not user_approval[field].strip():
                    errors.append(f"{shot_id}/{asset_id} user_approval.{field} 缺失")

    expected_word_count = len(selected_records)
    if summary.get("reused_frame_count") != reused_count:
        errors.append("summary.reused_frame_count 与逐镜 reuse 选中数量不一致")
    if summary.get("new_generation_count") != planned_generation_count:
        errors.append("summary.new_generation_count 与逐镜补生计划不一致")
    if args.stage == "pre-word" and summary.get("expected_word_image_count") != expected_word_count:
        errors.append("summary.expected_word_image_count 与逐镜实际选中图片数不一致")
    if args.stage == "pre-word":
        receipt = plan.get("gallery_receipt") if isinstance(plan.get("gallery_receipt"), dict) else {}
        if receipt.get("status") != "user_approved":
            errors.append("进入 Word 前 gallery_receipt.status 必须为 user_approved")
        for field in ("display_receipt_id", "displayed_at", "approved_at"):
            if not isinstance(receipt.get(field), str) or not receipt[field].strip():
                errors.append(f"进入 Word 前 gallery_receipt.{field} 缺失")
        expected_refs = [
            {"shot_id": shot_id, "asset_id": asset_id, "sha256": asset.get("sha256")}
            for shot_id, asset_id, asset in selected_records
        ]
        if receipt.get("asset_refs") != expected_refs:
            errors.append("gallery_receipt.asset_refs 必须按 Word 图片顺序精确绑定全部 asset_id 与 SHA-256")
    if not plan.get("scope", {}).get("historical_packages"):
        warnings.append("scope.historical_packages 为空；请确认已搜索用户点名的历史交付")

    if errors:
        print(f"资产复用审核阻断：{len(errors)}项")
        for item in errors:
            print(f"- {item}")
        for item in warnings:
            print(f"- 提醒：{item}")
        return 1

    print(
        "未发现资产复用结构性阻断："
        f"复用{reused_count}张，计划补生{planned_generation_count}张，"
        f"Word预期{summary.get('expected_word_image_count')}张。"
    )
    for item in warnings:
        print(f"- 提醒：{item}")
    print("此结果不替代人物身份、肖像授权、产品状态、动作语义和像素内容审核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
