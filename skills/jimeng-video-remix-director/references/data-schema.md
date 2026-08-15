# 数据结构

所有 JSON 使用 UTF-8、两空格缩进。未知值用 `null` 或 `unknown`，不要编造。

## project.json

`speech_strategy` 默认 `adaptive_from_script_and_source`。`allow_voiceover` 和 `allow_on_screen_speech` 表示允许范围，不代表实际镜头方式；实际选择保存在每镜 `audio.delivery_mode`。

## planning/story_plan.json

```json
{
  "schema_version": "1.1",
  "status": "reviewed",
  "subtitle_script": {
    "provided_by_user": true,
    "path": "source/subtitle.txt",
    "text": "用户字幕全文",
    "language": "zh-CN"
  },
  "source_style_assessment": {
    "delivery_style": "mixed",
    "observed_voiceover_ratio": 0.6,
    "observed_on_screen_speech_ratio": 0.3,
    "observed_silent_ratio": 0.1,
    "notes": ["原片产品特写由画外音覆盖"]
  },
  "narrative_logic": {
    "hook": "前三秒的具体钩子",
    "product_promise": "字幕承诺",
    "visual_proof": "产品画面如何证明",
    "eating_experience": "人物吃后的体验回报",
    "closing_payoff": "结尾落点"
  },
  "delivery_strategy": {
    "mode": "mixed",
    "rationale": "结合字幕语气和原片风格的理由",
    "voiceover_target_ratio": 0.6,
    "on_screen_speech_target_ratio": 0.3,
    "silent_target_ratio": 0.1
  },
  "visual_mix_targets": {
    "product_showcase": {"min_ratio": 0.3, "max_ratio": 0.5},
    "person_product_showcase": {"min_ratio": 0.2, "max_ratio": 0.45},
    "person_eating": {"min_ratio": 0.15, "max_ratio": 0.35}
  },
  "pacing": {
    "opening_hook_seconds": 3.0,
    "target_average_shot_seconds": 2.8,
    "maximum_single_shot_seconds": 5.0,
    "rhythm_notes": []
  },
  "segments": [
    {
      "id": "T001",
      "text": "字幕原文",
      "delivery_mode": "voiceover",
      "delivery_rationale": "材质特写比人物口型更重要",
      "assigned_shots": ["S001"]
    }
  ]
}
```

`delivery_style` 和策略 `mode`：`voiceover_dominant`、`on_screen_speech_dominant`、`mixed`、`silent`。镜头 `delivery_mode`：`voiceover`、`on_screen_speech`、`silent`。

## shots/shot_manifest.json

```json
{
  "schema_version": "1.1",
  "version": 1,
  "source_analysis_status": "reviewed",
  "shots": [
    {
      "id": "S001",
      "title": "镜头名称",
      "visual_type": "person_eating",
      "narrative_role": "eating_experience",
      "script_segment_ids": ["T003"],
      "scene_rationale": "该场景为何支持本镜头",
      "timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
      "purpose": "叙事或转化目的",
      "source_facts": [],
      "source_locks": [],
      "allowed_changes": ["只替换食物主体"],
      "scene": {"location": "场景", "background": [], "foreground": []},
      "character": {
        "present": true,
        "identity": "人物描述",
        "position": "位置",
        "gaze": "视线",
        "micro_expressions": []
      },
      "emotion": {
        "start": "起始情绪",
        "progression": [],
        "end": "结束情绪",
        "intensity": "natural"
      },
      "action_beats": [
        {
          "start": 0.0,
          "end": 1.0,
          "actor": "人物或产品",
          "action": "动作",
          "expression": "表情",
          "product_change": "产品变化",
          "camera_response": "镜头响应"
        }
      ],
      "product_state": {
        "profile": "durian-daifuku-v1",
        "state": "bitten",
        "count": "1",
        "packaging": "none",
        "shot_specific_traits": []
      },
      "camera": {
        "shot_size": "中近景",
        "angle": "平视",
        "movement": "基本固定",
        "focus": "人物与产品",
        "lens_feel": "真实手机镜头"
      },
      "lighting": {"source": "原片光线", "temperature": "warm", "notes": []},
      "audio": {
        "delivery_mode": "voiceover",
        "delivery_rationale": "选择理由",
        "script_text": "对应字幕原文",
        "speech_timing": null,
        "voice_direction": "语气、重音和停顿",
        "foley": [],
        "music": "低音量背景音乐"
      },
      "continuity": [],
      "hard_constraints": [],
      "prohibited": [],
      "asset_links": {
        "source_first_frame": "source/shot_frames/S001/source_first_frame.jpg",
        "beauty_keyframe_candidates": [],
        "selected_beauty_keyframe": null,
        "approved_generation_first_frame": null,
        "product_references": [],
        "avatar_reference": null
      },
      "risk": {"level": "high", "reasons": []},
      "status": "draft"
    }
  ]
}
```

产品展示镜头使用 `visual_type=product_showcase`，并设置 `character.present=false`；人物字段和情绪字段可为空。其他两类镜头必须设置 `character.present=true` 并填写人物与情绪。

## library/knowledge_index.json

```json
{
  "schema_version": "1.1",
  "version": 1,
  "entries": [
    {
      "id": "KB-001",
      "type": "prompt",
      "title": "人物吃大福的嘴部规则",
      "instruction": "咬下后闭口咀嚼，不展示口腔",
      "path": null,
      "applies_to": {
        "visual_type": "person_eating",
        "product_state": ["bitten"]
      },
      "tags": ["嘴部", "吃咬"],
      "priority": 90,
      "approved": true,
      "version": 1
    }
  ]
}
```

`type` 使用 `prompt`、`rule` 或 `image`。图片条目使用 `path`；规则条目使用 `instruction`。

## library/avatar_library.json

```json
{
  "schema_version": "1.1",
  "version": 1,
  "avatars": [
    {
      "id": "AV-001",
      "name": "数字人名称",
      "active": true,
      "portrait_rights_cleared": false,
      "usage_scope": "internal_test",
      "identity_traits": [],
      "forbidden_changes": [],
      "reference_assets": {
        "front": null,
        "left_45": null,
        "right_45": null,
        "profile": null,
        "expressions": []
      }
    }
  ],
  "selection_policy": {}
}
```

未清权利或未明确选择时，不绑定 `avatar_reference`。
