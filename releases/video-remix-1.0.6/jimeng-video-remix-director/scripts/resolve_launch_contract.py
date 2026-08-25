#!/usr/bin/env python3
"""Resolve source-intake versus full-delivery without treating normal inputs as failures."""
from __future__ import annotations

import argparse
import json
from typing import Any


def build_contract(
    *,
    source_video_provided: bool,
    dual_agent_requested: bool,
    full_delivery_requested: bool,
    revised_script_locked: bool,
    product_directive: str,
    product_reference_approved: bool,
) -> dict[str, Any]:
    if product_directive not in {"unspecified", "preserve", "replace"}:
        raise ValueError("product_directive must be unspecified, preserve or replace")

    product_mode = "replace_product" if product_directive == "replace" else "preserve_source_product"
    pending_inputs: list[str] = []
    if not revised_script_locked:
        pending_inputs.append("revised_script")
    if product_mode == "replace_product" and not product_reference_approved:
        pending_inputs.append("target_product_reference")

    replacement_ready = product_mode == "preserve_source_product" or product_reference_approved
    full_ready = full_delivery_requested and revised_script_locked and replacement_ready

    if not source_video_provided:
        execution_tier = "source_intake"
        launch_status = "awaiting_source_video"
    elif full_ready:
        execution_tier = "full_delivery"
        launch_status = "ready_for_full_delivery"
    else:
        execution_tier = "source_intake"
        launch_status = "ready_for_source_intake"

    return {
        "schema_version": "launch-contract-v1.0",
        "execution_tier": execution_tier,
        "launch_status": launch_status,
        "product_mode": product_mode,
        "create_dual_tasks": bool(dual_agent_requested and source_video_provided),
        "pending_inputs": pending_inputs,
        "pending_inputs_block_source_intake": False,
        "requires_locked_shot_map": execution_tier == "full_delivery",
        "requires_target_product_reference": product_mode == "replace_product" and execution_tier == "full_delivery",
        "controller_must_inline_transcript": execution_tier == "source_intake" and source_video_provided,
        "controller_must_inline_source_gallery": execution_tier == "source_intake" and source_video_provided,
        "image_branch_must_inline_progress": execution_tier == "source_intake" and source_video_provided,
        "source_intake_branch_scope": {
            "image": "source_visual_inventory_and_clickable_gallery",
            "text": "source_transcript_and_language_evidence",
        }
        if execution_tier == "source_intake"
        else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the smallest valid dual-Agent execution contract.")
    parser.add_argument("--source-video-provided", action="store_true")
    parser.add_argument("--dual-agent-requested", action="store_true")
    parser.add_argument("--full-delivery-requested", action="store_true")
    parser.add_argument("--revised-script-locked", action="store_true")
    parser.add_argument(
        "--product-directive",
        choices=("unspecified", "preserve", "replace"),
        default="unspecified",
    )
    parser.add_argument("--product-reference-approved", action="store_true")
    args = parser.parse_args()
    contract = build_contract(
        source_video_provided=args.source_video_provided,
        dual_agent_requested=args.dual_agent_requested,
        full_delivery_requested=args.full_delivery_requested,
        revised_script_locked=args.revised_script_locked,
        product_directive=args.product_directive,
        product_reference_approved=args.product_reference_approved,
    )
    print(json.dumps(contract, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
