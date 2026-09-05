#!/usr/bin/env python3
"""Regression tests for executable Douyin emotion and source-rhythm authoring."""

from __future__ import annotations

from pipeline import validate_commercial_emotion_rhythm, compiled_prompt_quality_errors, occurrence_phase_binding_errors


def codes(shot: dict, *, duration: float = 4.0, source_unit: bool = True) -> set[str]:
    issues: list[dict] = []
    validate_commercial_emotion_rhythm(
        shot,
        duration,
        {"enabled": True, "style": "douyin-food-commerce-v1"},
        issues,
        "shot",
        source_unit=source_unit,
    )
    return {item["code"] for item in issues}


def rich_shot() -> dict:
    return {
        "emotion": {
            "persona_drive": "像刚挖到宝一样，急着把好吃证据递到观众眼前",
            "primary_emotion": "挖到宝的兴奋",
            "secondary_emotions": ["馋意冒头", "被酥脆击中的惊喜"],
            "undertone": "怕观众不信，所以带一点认真证明的较真",
            "residue": "咽下前仍舍不得收住的回味和分享冲动",
            "emotion_vocabulary": ["眼睛发亮", "馋意冒头", "惊喜上扬", "回味黏住", "急着安利"],
            "commercial_turn": "先勾馋，再用脆裂证明，最后自然落到分享冲动",
            "evidence_basis": ["0.00秒主动前倾递近产品", "0.80秒听见脆裂后眉眼提亮"],
            "creative_enhancement": {"status": "none", "terms": [], "observable_execution": []},
        },
        "source_performance_layers": {
            "emotion_trigger": {"status": "observed"},
            "gaze": {"status": "observed"},
            "facial_microreaction": {"status": "observed"},
            "body_hand_preparation": {"status": "observed"},
            "breath_pause": {"status": "not_visible"},
            "voice_speech": {"status": "audible"},
        },
        "action_beats": [
            {"id": "B1", "start": 0.0, "end": 0.8, "action": "身体前倾，把产品递近镜头", "trigger": "开场抢注意", "emotion_terms": ["急着分享"], "visible_change": "眼睛发亮、眉峰提起", "voice_change": "起音快半拍", "next_action": "视线落到产品"},
            {"id": "B2", "start": 0.8, "end": 1.6, "action": "双手加力掰到脆裂", "trigger": "指腹受力", "emotion_terms": ["期待绷紧"], "visible_change": "唇角收紧后立刻上扬", "voice_change": "脆裂处停半拍", "next_action": "把断面转向镜头"},
            {"id": "B3", "start": 1.6, "end": 2.4, "action": "两半分离并亮出断面", "trigger": "听见清脆断响", "emotion_terms": ["被惊喜击中"], "visible_change": "双眼短促放大", "voice_change": "尾音上挑", "next_action": "送到嘴边"},
            {"id": "B4", "start": 2.4, "end": 3.2, "action": "咬下一口后产品离嘴", "trigger": "牙齿接触酥脆表层", "emotion_terms": ["馋意兑现"], "visible_change": "双颊开始轻鼓", "voice_change": "停止说话", "next_action": "闭口咀嚼"},
            {"id": "B5", "start": 3.2, "end": 4.0, "action": "闭口咀嚼并把剩余产品留在镜头内", "trigger": "酥香扩散", "emotion_terms": ["回味黏住"], "visible_change": "眼睑松下来、眉心舒展", "voice_change": "只留真实咀嚼声", "next_action": "看回镜头准备安利"},
        ],
    }


def main() -> int:
    good = rich_shot()
    assert not codes(good), codes(good)

    generic = rich_shot()
    generic["emotion"].update(
        primary_emotion="自然",
        secondary_emotions=["克制"],
        undertone="平稳",
        residue="轻微变化",
        emotion_vocabulary=["自然", "克制", "平稳", "真实"],
    )
    assert "COMMERCIAL_EMOTION_GENERIC_ONLY" in codes(generic)

    placeholder = rich_shot()
    placeholder["action_beats"][0]["action"] = "按各SRC和六层证据自然完成动作"
    assert "ACTION_BEAT_PLACEHOLDER" in codes(placeholder)

    gap = rich_shot()
    gap["action_beats"][1]["start"] = 1.0
    assert "ACTION_BEAT_TIMELINE_GAP" in codes(gap)

    overloaded = rich_shot()
    overloaded["action_beats"] = [{
        "id": "B1", "start": 0.0, "end": 4.0,
        "action": "拿起产品、掰断、露出断面、送入口中咬下、离嘴并闭口咀嚼后开口说话",
        "trigger": "开始", "emotion_terms": ["惊喜"], "visible_change": "自然变化",
        "voice_change": "自然", "next_action": "结束",
    }]
    overloaded_codes = codes(overloaded)
    assert "LONG_ACTION_BEAT_UNJUSTIFIED" in overloaded_codes
    assert "ACTION_BEAT_OVERLOADED" in overloaded_codes

    invented = rich_shot()
    invented["source_performance_layers"]["breath_pause"]["status"] = "template_supplement"
    assert "SOURCE_PERFORMANCE_TEMPLATE_INVENTION" in codes(invented)

    clean_prompt = "B1｜0.00–0.80秒：身体前倾递近产品。B2｜0.80–1.60秒：双手加力掰断。B3｜1.60–2.40秒：两半分离。B4｜2.40–3.20秒：咬下一口并离嘴。B5｜3.20–4.00秒：闭口咀嚼。"
    assert not compiled_prompt_quality_errors(clean_prompt, good), compiled_prompt_quality_errors(clean_prompt, good)
    padded = clean_prompt + "人物自然真实地完成动作。" * 5
    assert "PROMPT_REPETITIVE_PADDING" in compiled_prompt_quality_errors(padded, good)
    missing_beat = clean_prompt.replace("B4｜", "漏拍｜")
    assert "PROMPT_ACTION_BEAT_MISSING" in compiled_prompt_quality_errors(missing_beat, good)

    break_map = {"prepare": "B1", "tension": "B2", "snap": "B2", "separate": "B3", "reveal": "B3"}
    assert not occurrence_phase_binding_errors(break_map, good["action_beats"], ("prepare", "tension", "snap", "separate", "reveal"), allow_shared_adjacent=True)
    bad_break_map = {"prepare": "B2", "tension": "B1", "snap": "B2", "separate": "B3", "reveal": "B3"}
    assert "OCCURRENCE_PHASE_ORDER_INVALID" in occurrence_phase_binding_errors(bad_break_map, good["action_beats"], tuple(bad_break_map), allow_shared_adjacent=True)

    eating_map = {"approach": "B4", "bite_contact": "B4", "leave_mouth": "B4", "closed_mouth_chew": "B5"}
    assert not occurrence_phase_binding_errors(eating_map, good["action_beats"], tuple(eating_map), allow_shared_adjacent=True)
    missing_eating_map = {"approach": "B4", "bite_contact": "B4", "closed_mouth_chew": "NOPE"}
    assert "OCCURRENCE_PHASE_BEAT_MISSING" in occurrence_phase_binding_errors(missing_eating_map, good["action_beats"], tuple(missing_eating_map), allow_shared_adjacent=True)
    print("commercial emotion/rhythm regression tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
