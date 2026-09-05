#!/usr/bin/env python3
"""Regression tests for the no-blind-generation daifuku pixel gate."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from init_project import initialize_project, load_json, write_json  # noqa: E402
from pipeline import compile_shot, validate_durian_daifuku_v2_shot  # noqa: E402
from prepare_daifuku_pixel_preflight import prepare  # noqa: E402
from test_durian_daifuku_v2_contract import opening_shot  # noqa: E402


def assert_code(issues: list[dict], code: str) -> None:
    assert any(issue.get("code") == code for issue in issues), (code, issues)


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        project_dir = initialize_project(
            "daifuku-pixel-preflight-test",
            root,
            "durian-daifuku-v2",
            "ugc-food-review-v1",
            execution_tier="full_delivery",
        )
        product = load_json(project_dir / "library/product_bible.json")
        shot = opening_shot(product)
        source_path = project_dir / "source" / "frames" / "S001-source.png"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1200), (206, 194, 180)).save(source_path)
        shot["asset_links"]["source_first_frame"] = "source/frames/S001-source.png"
        manifest_path = project_dir / "shots" / "shot_manifest.json"
        manifest = load_json(manifest_path)
        manifest["shots"] = [shot]
        write_json(manifest_path, manifest)

        missing_issues: list[dict] = []
        validate_durian_daifuku_v2_shot(
            project_dir,
            product,
            shot,
            missing_issues,
            "shot",
            require_pixel_preflight=True,
        )
        assert_code(missing_issues, "DAIFUKU_PIXEL_PREFLIGHT_MISSING")

        pixel_plan = prepare(
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
                evidence="同景深接触食指中段横向实测20像素",
            )
        )
        assert pixel_plan["target"]["width_px"] == 75
        assert pixel_plan["target"]["height_px"] == 68
        assert pixel_plan["target"]["width_tolerance_px"] == [70, 80]
        assert pixel_plan["target"]["bbox_xywh"] == [362, 566, 75, 68]
        assert pixel_plan["anchor"]["measurement_method"] == "annotated_bbox"
        assert pixel_plan["anchor"]["measurement_bbox_xywh"] == [100, 500, 20, 60]
        assert pixel_plan["contract_binding"]["bundle_release_id"] == "video-remix-1.0.9"
        assert (project_dir / pixel_plan["guide_path"]).is_file()
        assert (project_dir / pixel_plan["manifest_path"]).is_file()

        prepared_shot = load_json(manifest_path)["shots"][0]
        valid_issues: list[dict] = []
        validate_durian_daifuku_v2_shot(
            project_dir,
            product,
            prepared_shot,
            valid_issues,
            "shot",
            require_pixel_preflight=True,
        )
        assert not valid_issues, valid_issues

        project = load_json(project_dir / "project.json")
        bundle = {
            "project": project,
            "product": product,
            "style": load_json(project_dir / "library/style_bible.json"),
            "story": {"break_plan": {"occurrences": []}},
            "corrections": {"rules": []},
            "knowledge": load_json(project_dir / "library/knowledge_index.json"),
        }
        markdown, _ = compile_shot(bundle, prepared_shot)
        for token in ("同景深锚点实测=20.0px", "目标大福=75×68px", "替换框xywh=[362, 566, 75, 68]", "绝不把青色轮廓"):
            assert token in markdown, token

        arithmetic_bad = copy.deepcopy(prepared_shot)
        arithmetic_bad["product_state"]["scale_lock"]["pixel_plan"]["target"]["width_px"] = 30
        issues: list[dict] = []
        validate_durian_daifuku_v2_shot(project_dir, product, arithmetic_bad, issues, "shot", require_pixel_preflight=True)
        assert_code(issues, "DAIFUKU_PIXEL_ARITHMETIC_INVALID")

        stale_state = copy.deepcopy(prepared_shot)
        stale_state["product_state"]["state"] = "pressed"
        stale_state["product_state"]["endpoint_lock"]["terminal_state"] = "pressed"
        issues = []
        validate_durian_daifuku_v2_shot(project_dir, product, stale_state, issues, "shot", require_pixel_preflight=True)
        assert_code(issues, "DAIFUKU_PIXEL_CONTRACT_STALE")

        guide_path = project_dir / pixel_plan["guide_path"]
        with guide_path.open("ab") as handle:
            handle.write(b"changed")
        issues = []
        validate_durian_daifuku_v2_shot(project_dir, product, prepared_shot, issues, "shot", require_pixel_preflight=True)
        assert_code(issues, "DAIFUKU_SCALE_GUIDE_HASH_MISMATCH")

    print(json.dumps({"status": "ok", "contract": "daifuku-pixel-preflight"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
