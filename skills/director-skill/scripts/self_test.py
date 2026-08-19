#!/usr/bin/env python3
"""Run a deterministic end-to-end test of project creation, planning, lint and compile."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from init_project import initialize_project  # noqa: E402
from pipeline import compile_project, lint_project  # noqa: E402


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="jimeng-skill-test-") as temp_dir:
        root = Path(temp_dir)
        project_dir = initialize_project(
            name="榴莲大福测试",
            output=root,
            product_profile="durian-daifuku-v1",
            style_profile="ugc-food-review-v1",
            project_id="test-project",
        )
        assert (project_dir / "planning" / "workflow_state.json").is_file()
        assert (project_dir / "planning" / "skill_update_candidates.json").is_file()
        assert (project_dir / "review" / "alignment_manifest.json").is_file()

        source_file = project_dir / "source" / "original.mp4"
        source_frame = project_dir / "source" / "test-source.jpg"
        beauty_frame = project_dir / "source" / "test-beauty.jpg"
        first_frame = project_dir / "source" / "test-approved.jpg"
        product_reference = project_dir / "source" / "product-reference.jpg"
        source_file.write_bytes(b"test-video-placeholder")
        for path, color in (
            (source_frame, (210, 180, 150)),
            (beauty_frame, (200, 170, 140)),
            (first_frame, (190, 160, 130)),
            (product_reference, (230, 210, 180)),
        ):
            Image.new("RGB", (180, 320), color).save(path)

        project = read_json(project_dir / "project.json")
        project["source_video"] = "source/original.mp4"
        write_json(project_dir / "project.json", project)

        source_manifest = read_json(project_dir / "source" / "source_manifest.json")
        source_manifest.update(
            {
                "source_video": "source/original.mp4",
                "sha256": "test-sha256",
                "duration": 4.0,
                "width": 1080,
                "height": 1920,
                "frame_rate": 30.0,
                "video_first_frame": "source/test-source.jpg",
            }
        )
        write_json(project_dir / "source" / "source_manifest.json", source_manifest)

        story_plan = read_json(project_dir / "planning" / "story_plan.json")
        story_plan.update(
            {
                "status": "reviewed",
                "subtitle_script": {
                    "provided_by_user": True,
                    "path": None,
                    "text": "你看这个冰皮，轻轻一拉就能看出它有多软糯。",
                    "language": "zh-CN",
                },
                "source_style_assessment": {
                    "delivery_style": "voiceover_dominant",
                    "observed_voiceover_ratio": 1.0,
                    "observed_on_screen_speech_ratio": 0.0,
                    "observed_silent_ratio": 0.0,
                    "notes": ["原片人物只展示产品，不直接讲话"],
                },
                "narrative_logic": {
                    "hook": "用拉伸动作快速建立软糯卖点",
                    "product_promise": "冰皮柔软而不形成细丝",
                    "visual_proof": "双手掰开并短距离拉伸",
                    "eating_experience": "由后续人物吃产品镜头承接",
                    "closing_payoff": "以满足表情确认口感",
                },
                "delivery_strategy": {
                    "mode": "voiceover_dominant",
                    "rationale": "保持原片人物只展示产品的风格，让字幕稿作为画外音覆盖动作证据。",
                    "voiceover_target_ratio": 1.0,
                    "on_screen_speech_target_ratio": 0.0,
                    "silent_target_ratio": 0.0,
                },
                "visual_mix_targets": {
                    "product_showcase": {"min_ratio": 0.0, "max_ratio": 0.0},
                    "person_product_showcase": {"min_ratio": 1.0, "max_ratio": 1.0},
                    "person_eating": {"min_ratio": 0.0, "max_ratio": 0.0},
                },
                "pacing": {
                    "opening_hook_seconds": 3.0,
                    "target_average_shot_seconds": 4.0,
                    "maximum_single_shot_seconds": 5.0,
                    "maximum_on_screen_chars_per_second": 5.0,
                    "maximum_voiceover_chars_per_second": 5.5,
                    "rhythm_notes": ["单镜头自测"],
                },
                "segments": [
                    {
                        "id": "T001",
                        "text": "你看这个冰皮，轻轻一拉就能看出它有多软糯。",
                        "delivery_mode": "voiceover",
                        "delivery_rationale": "画面动作承担证明，台词无需人物口型。",
                        "assigned_shots": ["S001"],
                    }
                ],
            }
        )
        write_json(project_dir / "planning" / "story_plan.json", story_plan)

        manifest = {
            "schema_version": "1.0",
            "version": 1,
            "source_analysis_status": "reviewed",
            "shots": [
                {
                    "id": "S001",
                    "title": "掰开并短距离拉伸",
                    "visual_type": "person_product_showcase",
                    "narrative_role": "hook",
                    "script_segment_ids": ["T001"],
                    "scene_rationale": "家庭餐厅环境延续原片生活感，让人物双手和断面成为唯一视觉重点。",
                    "timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
                    "purpose": "展示薄冰皮的柔软和短距离延展",
                    "source_facts": ["人物双手在胸前掰开原食物", "暖色家庭背景", "镜头基本固定"],
                    "source_locks": ["保持人物、双手位置、构图、机位、景深、暖色光和动作方向"],
                    "allowed_changes": ["只替换人物手中的食物主体"],
                    "scene": {
                        "location": "暖色家庭餐厅",
                        "background": ["浅米色墙面", "虚化木质家具", "暖黄色落地灯"],
                        "foreground": ["人物双手和榴莲大福"]
                    },
                    "character": {
                        "present": True,
                        "identity": "年轻亚洲女性，深色长发，浅色居家上衣",
                        "position": "画面中央偏上",
                        "gaze": "先看食物，完成拉伸后抬眼看镜头",
                        "micro_expressions": ["掰开前专注", "看到拉伸后眼睛略微睁大", "结尾轻轻点头"]
                    },
                    "emotion": {
                        "start": "专注期待",
                        "progression": ["拉开时短暂停顿", "看到真实拉带后自然惊喜"],
                        "end": "满足确认",
                        "intensity": "natural"
                    },
                    "action_beats": [
                        {
                            "start": 0.0,
                            "end": 1.2,
                            "actor": "人物双手",
                            "action": "从两侧轻轻捏住大福并缓慢向相反方向施力",
                            "expression": "低头专注观察",
                            "product_change": "完整外皮先产生柔软形变",
                            "camera_response": "保持固定中近景并把焦点落在双手"
                        },
                        {
                            "start": 1.2,
                            "end": 3.1,
                            "actor": "人物双手",
                            "action": "继续拉开至约4厘米并停留半秒",
                            "expression": "眼睛略微睁大，嘴角出现轻微惊喜",
                            "product_change": "形成2至4条短而宽的奶白糯米皮拉带并少量露出金黄榴莲馅",
                            "camera_response": "轻微转焦到拉带和不规则断面"
                        },
                        {
                            "start": 3.1,
                            "end": 4.0,
                            "actor": "人物双手和视线",
                            "action": "停止拉伸并让拉带轻微回缩，同时抬眼看镜头",
                            "expression": "满足地轻轻点头",
                            "product_change": "拉带弯曲并自然搭回馅料边缘",
                            "camera_response": "保持产品清晰并让人物脸部仍可辨认"
                        }
                    ],
                    "product_state": {
                        "profile": "durian-daifuku-v1",
                        "state": "stretched",
                        "count": "1",
                        "packaging": "none",
                        "shot_specific_traits": ["本镜头拉伸距离约4厘米"]
                    },
                    "camera": {
                        "shot_size": "正面中近景",
                        "angle": "与双手基本平齐",
                        "movement": "基本固定，仅在断面出现时轻微转焦",
                        "focus": "双手、白色冰皮拉带和少量榴莲馅",
                        "lens_feel": "真实手机近距离镜头"
                    },
                    "lighting": {
                        "source": "室内暖光和原片柔和正面光",
                        "temperature": "warm",
                        "notes": ["白色冰皮不能因暖光变黄", "人物和食物光向一致"]
                    },
                    "audio": {
                        "delivery_mode": "voiceover",
                        "delivery_rationale": "字幕稿解释拉伸卖点，人物画面专注展示动作。",
                        "script_text": "你看这个冰皮，轻轻一拉就能看出它有多软糯。",
                        "speech_timing": None,
                        "speech_capacity": {
                            "segment_count": 1,
                            "effective_characters": 19,
                            "speakable_seconds": 4.0,
                            "characters_per_second": 4.75,
                            "excluded_intervals": []
                        },
                        "voice_direction": "年轻女性自然画外音，像向熟人分享；前半句轻快，软糯二字轻微加重",
                        "foley": ["手指接触糯米皮的轻微软糯摩擦声", "安静室内底噪"],
                        "music": "低音量轻快无歌词音乐"
                    },
                    "continuity": ["人物服装和发型与前一镜一致", "产品外皮颜色保持奶白"],
                    "hard_constraints": ["人物不讲话", "无字幕", "无水印", "无包装", "只替换食物主体"],
                    "prohibited": ["蜘蛛网细丝", "黄色拉丝", "大量爆浆", "改变人物和构图"],
                    "asset_links": {
                        "source_first_frame": "source/test-source.jpg",
                        "beauty_keyframe_candidates": ["source/test-beauty.jpg"],
                        "selected_beauty_keyframe": "source/test-beauty.jpg",
                        "approved_generation_first_frame": "source/test-approved.jpg",
                        "product_references": ["source/product-reference.jpg"],
                        "avatar_reference": None
                    },
                    "risk": {"level": "high", "reasons": ["双手掰开", "产品拉伸", "不规则横截面"]},
                    "status": "reviewed"
                }
            ]
        }
        write_json(project_dir / "shots" / "shot_manifest.json", manifest)

        reuse_plan = read_json(project_dir / "planning" / "asset_reuse_plan.json")
        reuse_plan.update(
            {
                "status": "reviewed",
                "scope": {
                    "current_project": ".",
                    "historical_packages": [str(project_dir)],
                    "requested_operations": ["product_replace", "reuse_frames"],
                },
                "inventory": [
                    {
                        "asset_id": "FRAME-S001-APPROVED",
                        "asset_type": "approved_frame",
                        "library_layer": "scene_shot",
                        "path": "source/test-approved.jpg",
                        "sha256": hashlib.sha256(first_frame.read_bytes()).hexdigest(),
                        "width": 180,
                        "height": 320,
                        "approval_status": "approved",
                        "rights_status": "cleared",
                        "source_project": "test-project",
                        "source_shot_ids": ["S001"],
                        "avatar_ids": [],
                        "product_ids": ["durian-daifuku-v1"],
                        "product_states": ["stretched"],
                        "has_source_subtitles": False,
                        "has_watermark": False,
                        "is_composite_or_contact_sheet": False,
                    }
                ],
                "shot_decisions": [
                    {
                        "shot_id": "S001",
                        "decision": "reuse",
                        "candidate_asset_ids": ["FRAME-S001-APPROVED"],
                        "selected_asset_ids": ["FRAME-S001-APPROVED"],
                        "required_avatar_ids": [],
                        "required_product_ids": ["durian-daifuku-v1"],
                        "required_product_states": ["stretched"],
                        "identity_review": "not_applicable",
                        "product_review": "matched",
                        "scene_action_review": "matched",
                        "allowed_deterministic_transforms": [],
                        "generation_reason": None,
                        "candidate_rejection_reasons": [],
                    }
                ],
                "summary": {
                    "reused_frame_count": 1,
                    "new_generation_count": 0,
                    "rejected_asset_count": 0,
                    "expected_word_image_count": 1,
                },
            }
        )
        write_json(project_dir / "planning" / "asset_reuse_plan.json", reuse_plan)

        lint = lint_project(project_dir)
        assert lint["counts"]["ERROR"] == 0, lint
        compiled = compile_project(project_dir)
        assert compiled["shot_count"] == 1
        assert (project_dir / "prompts" / "S001.md").is_file()

        prompt_text = (project_dir / "prompts" / "S001.md").read_text(encoding="utf-8")
        assert "字幕稿在本镜头作为后期画外音" in prompt_text
        assert "人物展示产品" in prompt_text
        assert (project_dir / "review" / "shot_cards.md").is_file()

        prompt_path = project_dir / "prompts" / "S001.md"
        match = re.search(r"```text\n(.*?)\n```", prompt_text, re.S)
        assert match
        body = match.group(1)
        body += "测试" * ((3000 - len(re.sub(r"\s+", "", body)) + 1) // 2)
        prompt_path.write_text(prompt_text[: match.start(1)] + body + prompt_text[match.end(1) :], encoding="utf-8")
        docx_path = project_dir / "exports" / "test.docx"
        exported = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "export_jimeng_docx.py"),
                "--project-dir",
                str(project_dir),
                "--out",
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert exported.returncode == 0, exported.stdout + exported.stderr
        export_manifest = read_json(docx_path.with_suffix(".manifest.json"))
        assert export_manifest["embedded_media_count"] == 1
        assert export_manifest["reused_frame_count"] == 1

        aligned = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "align_exports.py"),
                "--project-dir",
                str(project_dir),
                "--docx",
                str(docx_path),
                "--require-docx",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert aligned.returncode == 0, aligned.stdout + aligned.stderr
        alignment = read_json(project_dir / "review" / "alignment_manifest.json")
        assert alignment["summary"]["status"] == "aligned"
        assert alignment["shots"][0]["checks"]["prompt_text_aligned"] is True
        assert alignment["shots"][0]["checks"]["frame_aligned"] is True
        assert (project_dir / "exports" / "完整逐分镜Prompt.txt").is_file()
        assert len(list((project_dir / "exports" / "shots").glob("S001_*.txt"))) == 1

        candidates = read_json(project_dir / "planning" / "skill_update_candidates.json")
        candidates["candidates"] = [
            {
                "candidate_id": "RULE-TEST-001",
                "category": "alignment",
                "observed_problem": "测试问题",
                "proposed_rule": "每次导出后核对唯一事实源哈希。",
                "scope": "cross_project",
                "evidence": ["review/alignment_manifest.json"],
                "target_skill": "director-skill",
                "target_resource": "references/workflow.md",
                "status": "new",
                "user_approved": False,
            }
        ]
        write_json(project_dir / "planning" / "skill_update_candidates.json", candidates)
        reviewed = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "review_skill_candidates.py"), "--project-dir", str(project_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
        assert (project_dir / "review" / "skill_update_report.md").is_file()

    print("SELF TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
