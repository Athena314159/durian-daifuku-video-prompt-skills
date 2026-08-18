#!/usr/bin/env python3
"""Regression tests for lint_prompt_txt.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lint_prompt_txt import lint


BASE_GOOD_PROMPT = """4:3真实手机生活拍摄。视频核心是男生藏着礼物回家、等镜头后的女生反应。男生始终在画面中；女生始终在镜头后，不出镜，不生成她的脸、嘴、身体、影子或倒影。男生先看盒子，再抬眼看向镜头后的女生，视线在礼物与她之间移动。听见她问话后，他眉峰短促抬起、眼睑放松、鼻翼轻动、嘴角压住笑；肩颈先绷紧再放松，重心前移，手指扣住盒底、手腕轻转。开口前短促吸气，停顿半拍再换气；采用轻微川渝年轻情侣城市口音，声音中低，平翘舌不刻意咬得过正，起音轻，语速稍快，重音落在礼物名称，尾音带笑，不夸张模仿方言。场景保留普通住宅暖顶灯、门口脚步、包装摩擦和手机自动对焦。

0.00–1.00秒：女生在镜头后说“今天七夕几点了才回来？”。男生听见后停止手部动作，抬眼看向镜头后的女生，嘴角想笑又压住。

从第一帧到最后一帧，禁止出现任何字幕和水印。
"""


def build_valid_prompt() -> str:
    """Create a 3000–4000-character positive fixture with distinct execution checks."""
    dimensions = (
        "人物视线和眉眼反应",
        "肩颈重心与衣料惯性",
        "手指受力和包装形变",
        "产品数量与破损连续性",
        "手机焦点和景深过渡",
        "室内光向与肤色曝光",
        "现场声音和呼吸停顿",
        "镜头起止状态可衔接性",
    )
    details = []
    for index in range(44):
        dimension = dimensions[index % len(dimensions)]
        details.append(
            f"执行检查{index + 1:02d}：逐帧核对{dimension}，动作必须有准备、发生、受力反馈和稳定落点，"
            "不得用跳帧、悬空、突然变形或无来源位移替代连续运动。"
        )
    return BASE_GOOD_PROMPT + "\n".join(details)


GOOD_PROMPT = build_valid_prompt()


def make_txt(*, speaker: str = "女生", extra_prompt: str = "", product: str = "完整未破、手持展示") -> str:
    prompt = GOOD_PROMPT + extra_prompt
    char_count = len("".join(prompt.split()))
    return f"""==================================================
S001｜“今天七夕几点了才回来？”
==================================================
原片时间：00:00.000–00:03.000
独立生成时长：3.000秒
人物位置：男生始终在画面中；女生始终在镜头后且不出镜
声音方式：女生镜外现场对白
产品形态：{product}
生成首帧：待制作

【口播稿】
{speaker}：“今天七夕几点了才回来？”

【完整Prompt｜主体非空白字符数：{char_count}】
{prompt}
【原片动作对应】
- 原片 00:00.000–00:01.000 的男生停步和抬眼，对应生成镜内 0.00–1.00 秒。

【内容审核记录】
- 人物事实：男生在画面中；女生在镜头后。
- 台词与声源事实：该句由女生在镜外说。
- 生成后像素复核：女生不得出镜。
"""


def make_role_lock(*, required_actions: list[dict[str, str]] | None = None) -> dict:
    return {
        "characters": {
            "A": {"label": "女生", "visibility": "offscreen_all", "camera_holder": True},
            "B": {"label": "男生", "visibility": "onscreen_all", "camera_holder": False},
        },
        "spatial_invariants": ["女生全程镜头后", "男生全程画面中"],
        "speech_plan": {
            "source": "creative_proposal",
            "disclosed_to_user": True,
            "summary": "轻微川渝年轻情侣城市口音；平翘舌不刻意咬正，语速中快，尾音带笑，不夸张模仿方言"
        },
        "dialogue": [
            {
                "speaker_id": "A",
                "label": "女生",
                "text": "今天七夕几点了才回来？",
                "delivery": "offscreen_live",
                "visible_lip_sync": False,
                "action_subject": "男生",
                "visual_evidence": "男生抬眼看向镜头后的女生",
            }
        ],
        "required_actions": required_actions or [],
    }


class PromptLintRegressionTests(unittest.TestCase):
    def run_lint(self, txt: str, role_lock: dict | None = None):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            txt_path = root / "prompt.txt"
            txt_path.write_text(txt, encoding="utf-8")
            role_path = None
            if role_lock is not None:
                role_path = root / "role_lock.json"
                role_path.write_text(json.dumps(role_lock, ensure_ascii=False), encoding="utf-8")
            return lint(txt_path, role_path)

    def assert_has_code(self, issues, code: str):
        self.assertIn(code, {issue.code for issue in issues}, [issue.message for issue in issues])

    def test_correct_onscreen_man_offscreen_woman(self):
        self.assertEqual([], self.run_lint(make_txt(), make_role_lock()))

    def test_prompt_below_3000_is_blocked(self):
        text = make_txt().replace(GOOD_PROMPT, BASE_GOOD_PROMPT)
        issues = self.run_lint(text, make_role_lock())
        self.assert_has_code(issues, "PROMPT_TOO_SHORT")

    def test_declared_prompt_count_must_match_computed_count(self):
        text = make_txt().replace(
            f"主体非空白字符数：{len(''.join(GOOD_PROMPT.split()))}",
            "主体非空白字符数：3000",
        )
        issues = self.run_lint(text, make_role_lock())
        self.assert_has_code(issues, "PROMPT_CHAR_COUNT_MISMATCH")

    def test_wrong_speaker_is_blocked(self):
        issues = self.run_lint(make_txt(speaker="男生"), make_role_lock())
        self.assert_has_code(issues, "DIALOGUE_SPEAKER_MISMATCH")

    def test_offscreen_character_visibility_leak_is_blocked(self):
        issues = self.run_lint(make_txt(extra_prompt="女生的侧脸进入画面并清晰可见。"), make_role_lock())
        self.assert_has_code(issues, "OFFSCREEN_CHARACTER_VISIBLE")

    def test_internal_product_label_and_status_word_are_blocked(self):
        issues = self.run_lint(make_txt(extra_prompt="本镜审核写PASS。", product="V2 person_eating"), make_role_lock())
        self.assert_has_code(issues, "PRODUCT_STATE_LABEL_NOT_CHINESE")
        self.assert_has_code(issues, "STRUCTURE_RESULT_MISREPRESENTED_AS_CONTENT_AUDIT")

    def test_missing_bite_chain_is_blocked_when_source_requires_it(self):
        role = make_role_lock(required_actions=[{"shot_id": "S001", "kind": "bite_chain"}])
        issues = self.run_lint(make_txt(), role)
        self.assert_has_code(issues, "SOURCE_ACTION_OMITTED")

    def test_abstract_emotion_without_six_layers_is_blocked(self):
        text = make_txt().replace(GOOD_PROMPT, "0.00–1.00秒：男生自然开心地展示礼物。禁止出现任何字幕和水印。")
        issues = self.run_lint(text, make_role_lock())
        self.assert_has_code(issues, "PERFORMANCE_DETAIL_MISSING")

    def test_generic_speech_placeholder_is_blocked(self):
        text = make_txt().replace(
            "采用轻微川渝年轻情侣城市口音，声音中低，平翘舌不刻意咬得过正，起音轻，语速稍快，重音落在礼物名称，尾音带笑，不夸张模仿方言。",
            "声音中低，起音轻，语速稍快，重音落在礼物名称，尾音带笑，沿用原片生活口语节奏。",
        )
        issues = self.run_lint(text, make_role_lock())
        self.assert_has_code(issues, "GENERIC_SPEECH_PLACEHOLDER")
        self.assert_has_code(issues, "ACCENT_PLAN_MISSING")

    def test_undisclosed_creative_accent_proposal_is_blocked(self):
        role = make_role_lock()
        role["speech_plan"]["disclosed_to_user"] = False
        issues = self.run_lint(make_txt(), role)
        self.assert_has_code(issues, "ACCENT_PROPOSAL_NOT_DISCLOSED")

    def test_butter_crisp_bare_product_requires_physical_microstructure(self):
        issues = self.run_lint(make_txt(product="黄油脆丝棒完整未破、手持展示"), make_role_lock())
        self.assert_has_code(issues, "PRODUCT_MICROSTRUCTURE_RULE_MISSING")

    def test_butter_crisp_box_requires_confirmed_dimensions_and_ratios(self):
        issues = self.run_lint(make_txt(product="黄油脆丝棒未开封零售外盒"), make_role_lock())
        self.assert_has_code(issues, "PACKAGE_DIMENSION_LOCK_MISSING")

    def test_butter_crisp_structure_and_box_dimensions_can_pass(self):
        extra = (
            "黄油脆丝棒六面是实体片状脆丝覆盖层，每块有自身厚度和侧边厚度，形成前后遮挡、"
            "重叠缝隙与不规则窄缝；少量碎片轮廓凸出并打破干净外轮廓。禁止平面贴图、"
            "印刷图案、浅浮雕、压花或光滑橙色基底。外盒严格15×15×4.5 cm，正面1:1正方形，"
            "厚度约为正面边长30%，是一只扁方盒。"
        )
        issues = self.run_lint(
            make_txt(product="黄油脆丝棒完整未破手持与未开封零售外盒", extra_prompt=extra),
            make_role_lock(),
        )
        blocked = {issue.code for issue in issues}
        self.assertNotIn("PRODUCT_MICROSTRUCTURE_RULE_MISSING", blocked)
        self.assertNotIn("PACKAGE_DIMENSION_LOCK_MISSING", blocked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
