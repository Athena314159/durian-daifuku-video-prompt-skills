#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pipeline import compact_controller_prompt, compiled_prompt_quality_errors


def main():
    project = {"aspect_ratio": "9:16"}
    story = {"controller_prompt_blueprints": [{
        "shot_id": "S001",
        "prompt_blueprint": "S003由两个独立源unit组成，SRC002后3.50秒硬切SRC003；旧Word图只作构图参考。人物持完整大福。",
    }]}
    shot = {
        "id": "S001",
        "controller_prompt_mode": "compact_v1",
        "purpose": "同期口播后咬食。",
        "timecode": {"duration": 10},
        "audio": {"delivery_mode": "on_screen_speech", "script_text": "测试口播", "speech_timing": "咬前说完。", "foley": "软皮受压声"},
        "camera": {"shot_size": "近景", "angle": "平视", "movement": "固定", "focus": "手口产品"},
        "lighting": {"source": "室内暖光", "temperature": "暖中性"},
        "product_state": {"state": "bitten"},
        "action_beats": [{"id": "S001-A01", "start": 0, "end": 10, "action": "先说话再咬食", "voice_change": "咬前口播", "product_change": "完整体形成单一咬口"}],
    }
    prompt = compact_controller_prompt(project, story, shot)
    assert prompt
    for header in ("【生成目标与叙事职责】", "【口播原文与声源】", "【原片叙事复原】", "【原片逐时动作】", "【产品与动作物理】", "【摄影、灯光与声音】", "【最小纠错附录】"):
        assert prompt.count(header) == 1
    assert "SRC002" not in prompt and "SRC003" not in prompt and "旧Word" not in prompt
    assert "S001-A01" not in prompt
    assert not compiled_prompt_quality_errors(prompt, shot)
    assert len("".join(prompt.split())) < 4000
    print("compact controller prompt test passed")


if __name__ == "__main__":
    main()
