#!/usr/bin/env python3
"""Regression tests for user-visible gallery approval and package integration QA."""

from __future__ import annotations

from validate_branch_handoff import validate_ready_gallery_approval, validate_package_integration_qa


SHA = "a" * 64


def main() -> int:
    approved = [("SRC001", [("A1", "/tmp/a.png", SHA)])]
    errors: list[str] = []
    validate_ready_gallery_approval({}, approved, errors)
    assert any("gallery_receipt" in item for item in errors)

    receipt = {
        "status": "user_approved",
        "display_receipt_id": "gallery-20260824-001",
        "displayed_at": "2026-08-24T12:00:00+08:00",
        "approved_at": "2026-08-24T12:01:00+08:00",
        "asset_refs": [{"unit_id": "SRC001", "asset_id": "A1", "sha256": SHA}],
    }
    errors = []
    validate_ready_gallery_approval(receipt, approved, errors)
    assert not errors, errors

    receipt["asset_refs"][0]["sha256"] = "b" * 64
    errors = []
    validate_ready_gallery_approval(receipt, approved, errors)
    assert any("exact approved asset order/hash" in item for item in errors)

    unit = {
        "unit_id": "SRC001",
        "packaging_evidence": {"visible": True},
        "qa": {
            "package_integration": {
                "box_measurements": [{"box_id": "BOX1", "front_width_height_ratio": 1.0, "thickness_front_ratio": 0.30, "same_size_as_peer_boxes": True}],
                "scene_light_match": "matched",
                "contact_shadow": "matched",
                "edge_blend": "matched",
                "flat_cutout": False,
                "observable_evidence": "盒体右下接触影与人物鼻影同向，折边高光连续",
            }
        },
    }
    errors = []
    validate_package_integration_qa(unit, "units[0]", errors)
    assert not errors, errors

    unit["qa"]["package_integration"]["contact_shadow"] = "missing"
    errors = []
    validate_package_integration_qa(unit, "units[0]", errors)
    assert any("contact_shadow" in item for item in errors)

    unit["qa"]["package_integration"]["contact_shadow"] = "matched"
    unit["qa"]["package_integration"]["box_measurements"][0]["thickness_front_ratio"] = 0.10
    errors = []
    validate_package_integration_qa(unit, "units[0]", errors)
    assert any("thickness_front_ratio" in item for item in errors)
    print("image user-approval/package-integration regression tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
