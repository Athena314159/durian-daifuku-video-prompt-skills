#!/usr/bin/env python3
from __future__ import annotations

from pipeline import validate_semantic_consistency


def main() -> int:
    story = {
        "source_style_assessment": {"delivery_style": "on_screen_speech_dominant", "observed_voiceover_ratio": 0.0},
        "delivery_strategy": {"voiceover_target_ratio": 0.3},
        "segments": [{"text": "一口爆浆", "claim_semantics": {"mode": "literal", "visual_literalization": True}}],
    }
    shots = [
        {"id": "S1", "purpose": "展示互补两半", "audio": {"delivery_mode": "voiceover"}, "product_state": {"state": "opening_window_seed", "filling_lock": {"stringing": True}}},
        {"id": "S2", "purpose": "人物端盘展示互补两半", "audio": {"delivery_mode": "silent"}, "product_state": {"state": "plated"}},
        {"id": "S3", "purpose": "人物端盘展示四颗完整大福，不出现互补两半", "audio": {"delivery_mode": "silent"}, "product_state": {"state": "plated"}},
        {
            "id": "S4",
            "purpose": "只生成无品牌运输保护载体首帧",
            "audio": {"delivery_mode": "silent"},
            "product_state": {"state": "whole"},
            "hard_constraints": ["精确0秒首帧必须承接透明印刷袋、2×2四枚大福。"],
            "source_units": [{
                "source_shot_id": "SRC004",
                "exact_first_frame_generation_contract": {"product_visibility": "absent", "visible_target_product_count": 0},
                "delivery_asset_roles": {"SRC004-A01": "Exact 0.00 s frame: transparent printed bag with 4 daifuku."},
            }],
        },
        {
            "id": "S5",
            "purpose": "只生成无品牌运输保护载体首帧",
            "audio": {"delivery_mode": "silent"},
            "product_state": {"state": "whole"},
            "hard_constraints": ["精确0秒首帧移除旧印刷袋并中和为无品牌载体；目标可见大福0、零售盒0。"],
            "source_units": [{
                "source_shot_id": "SRC005",
                "exact_first_frame_generation_contract": {"product_visibility": "absent", "visible_target_product_count": 0},
                "delivery_asset_roles": {"SRC005-A01": "首帧中和旧产品包装，product absent，不出现目标产品。"},
            }],
        },
    ]
    issues = []
    validate_semantic_consistency(story, shots, issues)
    codes = {item["code"] for item in issues}
    expected = {"AUDIO_SOURCE_MODE_CONTRADICTION", "UNSUPPORTED_VOICEOVER_INVENTED", "DAIFUKU_STATE_NARRATIVE_CONTRADICTION", "DAIFUKU_PLATED_PURPOSE_CONTRADICTION", "DAIFUKU_FILLING_STRINGING_FORBIDDEN", "SPOKEN_HYPERBOLE_VISUALIZED", "EXACT_FIRST_FRAME_STALE_SHOT_TEXT_CONFLICT"}
    assert expected <= codes, sorted(codes)
    assert not any(item["code"] == "DAIFUKU_PLATED_PURPOSE_CONTRADICTION" and "S3" in item["path"] for item in issues), issues
    assert sum(item["code"] == "EXACT_FIRST_FRAME_STALE_SHOT_TEXT_CONFLICT" for item in issues) == 2, issues
    assert not any(item["code"] == "EXACT_FIRST_FRAME_STALE_SHOT_TEXT_CONFLICT" and "S5" in item["path"] for item in issues), issues
    print("semantic consistency regressions: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
