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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(raw: str, project_root: Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else project_root / path


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
