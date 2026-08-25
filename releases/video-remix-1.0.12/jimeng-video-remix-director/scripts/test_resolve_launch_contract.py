#!/usr/bin/env python3
"""Regression tests for phase and product-mode resolution."""
from __future__ import annotations

from resolve_launch_contract import build_contract


def contract(**overrides):
    values = {
        "source_video_provided": True,
        "dual_agent_requested": True,
        "full_delivery_requested": False,
        "revised_script_locked": False,
        "product_directive": "unspecified",
        "product_reference_approved": False,
    }
    values.update(overrides)
    return build_contract(**values)


def main() -> None:
    vague = contract()
    assert vague["execution_tier"] == "source_intake"
    assert vague["product_mode"] == "preserve_source_product"
    assert vague["launch_status"] == "ready_for_source_intake"
    assert vague["pending_inputs"] == ["revised_script"]
    assert vague["create_dual_tasks"] is True
    assert vague["controller_must_inline_transcript"] is True
    assert vague["controller_must_inline_source_gallery"] is True
    assert vague["image_branch_must_inline_progress"] is True
    assert vague["source_intake_branch_scope"]["image"] == "source_visual_inventory_and_clickable_gallery"
    assert vague["pending_inputs_block_source_intake"] is False

    explicit_replace_missing_reference = contract(
        full_delivery_requested=True,
        revised_script_locked=True,
        product_directive="replace",
    )
    assert explicit_replace_missing_reference["execution_tier"] == "source_intake"
    assert explicit_replace_missing_reference["launch_status"] == "ready_for_source_intake"
    assert explicit_replace_missing_reference["pending_inputs"] == ["target_product_reference"]
    assert explicit_replace_missing_reference["product_mode"] == "replace_product"

    preserve_full = contract(full_delivery_requested=True, revised_script_locked=True)
    assert preserve_full["execution_tier"] == "full_delivery"
    assert preserve_full["product_mode"] == "preserve_source_product"
    assert preserve_full["pending_inputs"] == []
    assert preserve_full["requires_target_product_reference"] is False
    assert preserve_full["controller_must_inline_source_gallery"] is False
    assert preserve_full["image_branch_must_inline_progress"] is False

    replace_full = contract(
        full_delivery_requested=True,
        revised_script_locked=True,
        product_directive="replace",
        product_reference_approved=True,
    )
    assert replace_full["execution_tier"] == "full_delivery"
    assert replace_full["requires_target_product_reference"] is True

    missing_video = contract(source_video_provided=False)
    assert missing_video["launch_status"] == "awaiting_source_video"
    assert missing_video["create_dual_tasks"] is False

    print("LAUNCH CONTRACT TESTS PASSED: 5 cases")


if __name__ == "__main__":
    main()
