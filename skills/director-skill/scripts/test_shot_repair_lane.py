#!/usr/bin/env python3
"""Regression test for one-unit repair without a project-wide recompile."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pipeline
from pipeline import REQUIRED_FILES, compile_shot_repair


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shot-repair-test-") as temporary:
        root = Path(temporary)
        project = {
            "project_id": "P001",
            "execution_tier": "first_frame_only",
            "skill_release_lock": {
                "bundle_release_id": pipeline.current_release_manifest()["bundle_release_id"],
                "prompt_authoring_contract": "narrative-six-layer-v1",
                "auto_upgrade": False,
            },
        }
        write_json(root / "project.json", project)
        write_json(
            root / "shots/shot_manifest.json",
            {
                "shots": [
                    {
                        "id": "S001",
                        "title": "局部返工",
                        "timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
                        "source_units": [
                            {
                                "source_shot_id": "SRC001",
                                "source_timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
                                "generation_timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
                                "source_first_frame": "source/SRC001.png",
                                "storyboard_description": "只返工这一镜",
                                "script_text": "无",
                            }
                        ],
                        "inserted_units": [],
                        "asset_links": {"source_first_frame": "source/SRC001.png"},
                    }
                ]
            },
        )
        for relative in REQUIRED_FILES.values():
            path = root / relative
            if not path.is_file():
                write_json(path, {})
        (root / "source/SRC001.png").parent.mkdir(parents=True, exist_ok=True)
        (root / "source/SRC001.png").write_bytes(b"source-frame")

        bundle = {
            "project": project,
            "product": {"profile_id": "target", "name": "目标产品"},
            "product_library": {},
            "style": {"profile_id": "style"},
            "corrections": {"rules": []},
            "knowledge": {"entries": []},
            "avatars": {"avatars": []},
            "story": {},
            "asset_reuse": {},
            "source": {},
            "shots": json.loads((root / "shots/shot_manifest.json").read_text(encoding="utf-8")),
        }
        original_read_bundle = pipeline.read_bundle
        original_compile_shot = pipeline.compile_shot
        pipeline.read_bundle = lambda _project_dir: bundle
        pipeline.compile_shot = lambda _bundle, shot: (
            "# S001\n\n```text\n【生成目标与叙事职责】\n局部修复。\n【产品与动作物理】目标产品。\n【生图硬性规则】GENERATION_HARD_RULES_V1；无字幕、无水印。\n```\n",
            {"shot_id": "S001", "correction_rule_ids": ["R1"], "knowledge_entry_ids": ["K1"]},
        )
        try:
            result = compile_shot_repair(root, "S001", "SRC001")
        finally:
            pipeline.read_bundle = original_read_bundle
            pipeline.compile_shot = original_compile_shot

        assert result["status"] == "ready_for_image_authorization"
        assert result["global_pack_touched"] is False
        assert result["unit_id"] == "SRC001"
        assert Path(result["prompt_file"]).is_file()
        assert Path(result["receipt"]).is_file()
        assert not (root / "prompts/generation_pack.json").exists()
        assert not (root / "review/prompt_delivery_receipt.json").exists()
    print("shot repair lane regression test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

