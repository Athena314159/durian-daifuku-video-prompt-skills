#!/usr/bin/env python3
"""Regression tests for generation-time hard rules and feedback write-through."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from image_generation_gate import validate_generation_prompt_text
from correction_memory import normalize_memory


ROOT = Path(__file__).resolve().parent
WRITEBACK = ROOT / "apply_generation_feedback.py"


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        project = Path(temporary)
        memory = project / "library" / "correction_memory.json"
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text(json.dumps({"schema_version": "1.0", "version": 1, "rules": []}), encoding="utf-8")
        command = [
            sys.executable,
            str(WRITEBACK),
            "--project-dir",
            str(project),
            "--scope",
            "shot",
            "--target",
            "S001",
            "--instruction",
            "包装必须用批准母版投射，清除原包装并保持可见面不变。",
            "--evidence",
            "review/gallery/S001.png",
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        payload = json.loads(memory.read_text(encoding="utf-8"))
        assert payload["rules"][0]["active"] is True
        assert payload["rules"][0]["origin"] == "user_feedback"
        assert (project / "review" / "feedback-writeback").is_dir()

        prompt = """【生成目标与叙事职责】从同一原始首帧生成。
【产品与动作物理】目标产品。
【生图硬性规则】GENERATION_HARD_RULES_V1；目标产品只保留，原产品清除；无字幕、无水印；严格从同一原图生成；包装必须用批准母版投射，清除原包装并保持可见面不变。
"""
        errors = validate_generation_prompt_text(
            prompt,
            {"product_mode": "replace_product", "product_profile": "target-profile"},
            {"name": "目标产品", "profile_id": "target-profile"},
            {"id": "S001"},
            {"product_visibility": "present"},
            [],
            payload["rules"],
            [],
        )
        assert errors == [], errors

        legacy = {
            "schema_version": "1.0",
            "version": 2,
            "rules": [
                {
                    "id": "CORR-OVERLAY-001",
                    "scope": "all_frames",
                    "trigger": "source frame contains watermark",
                    "rule": "清除原视频字幕、水印和平台UI，并且不生成任何新文字。",
                },
                {
                    "id": "CORR-DAIFUKU-GEOMETRY-001",
                    "scope": "whole_or_held_daifuku",
                    "rule": "大福保持约7厘米、饱满略扁圆，不得变成薄片或方块。",
                },
            ],
        }
        normalized, changed = normalize_memory(
            legacy,
            project_id="P001",
            product_profile="durian-daifuku-v2",
            style_profile="ugc-food-review-v1",
        )
        assert changed is True
        assert [rule["scope"] for rule in normalized["rules"]] == ["project", "product"]
        assert all(rule["active"] is True and rule["instruction"] for rule in normalized["rules"])
        legacy_prompt = """【生成目标与叙事职责】从同一原始首帧生成。
【产品与动作物理】目标产品 durian-daifuku-v2。
【生图硬性规则】GENERATION_HARD_RULES_V1；目标产品只保留，原产品清除；无字幕、无水印；严格从同一原图生成；清除原视频字幕、水印和平台UI，并且不生成任何新文字。大福保持约7厘米、饱满略扁圆，不得变成薄片或方块。暖奶白、果泥、糯米粉雾。
"""
        assert validate_generation_prompt_text(
            legacy_prompt,
            {"project_id": "P001", "product_mode": "replace_product", "product_profile": "durian-daifuku-v2"},
            {"name": "榴莲大福", "profile_id": "durian-daifuku-v2"},
            {"id": "S001"},
            {"product_visibility": "present"},
            [],
            normalized["rules"],
            [],
        ) == []
    print("generation hard-rule/writeback regression tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
