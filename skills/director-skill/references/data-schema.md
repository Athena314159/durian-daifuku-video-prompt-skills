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
    "maximum_on_screen_chars_per_second": 5.0,
    "maximum_voiceover_chars_per_second": 5.5,
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
        "speech_capacity": {
          "segment_count": 1,
          "effective_characters": 18,
          "speakable_seconds": 4.0,
          "characters_per_second": 4.5,
          "excluded_intervals": ["1.20–2.10秒闭口咀嚼与吞咽"]
        },
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
        "avatar_reference": null,
        "edit_chain": {
          "face_edit_enabled": false,
          "face_reference_ids": [],
          "approved_first_frame_review": "pending",
          "pixel_protection": ["未授权人物", "未经授权的文字与品牌资产"],
          "notes": "从 source_first_frame 单次编辑；不在失败编辑图上叠修。"
        }
      },
      "risk": {"level": "high", "reasons": []},
      "status": "draft"
    }
  ]
}
```

口播有效字符只计汉字、字母和数字。`speakable_seconds` 必须扣除屏内人物入口、咬合、闭口咀嚼、吞咽、必要换气、纯拟音和无声观察；`characters_per_second` 必须等于有效字符数除以实际可说时段。默认每镜不超过3个台词句段，单镜不超过5秒，屏内口播不超过5.0字/秒，画外音不超过5.5字/秒。Prompt字符下限不得成为合并镜头的理由。

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

## library/product_library.json

产品库保存可跨项目复用的产品与包装身份资产；项目仍只选择一个目标产品，并把选中版本的硬约束写入 `product_bible.json`。

```json
{
  "schema_version": "1.0",
  "version": 1,
  "products": [
    {
      "id": "PR-001",
      "name": "产品名称",
      "active": true,
      "rights_cleared": true,
      "usage_scope": "commercial",
      "profile_path": "library/product_bible.json",
      "version": 1,
      "states": ["完整体", "开袋", "断面", "咬后"],
      "reference_assets": [
        {
          "id": "PR-001-DETAIL",
          "role": "product_structure_reference",
          "path": "source/references/product-detail.jpg",
          "sha256": "<sha256>",
          "approved": true
        }
      ],
      "approved_result_assets": []
    }
  ],
  "selection_policy": {
    "one_target_product_per_project": true
  }
}
```

参考图只承担声明的身份、结构、材质、包装或状态职责，不自动成为可直接交付的分镜帧。`approved_result_assets` 必须记录原项目、旧镜头、产品状态、人物/场景依赖、尺寸、哈希和审核结论，才能进入资产复用计划。

## planning/asset_reuse_plan.json

该文件位于人物库、产品库与逐镜交付之间。任何换脸、换产品、换包装、换场景、重拆镜、补图或 Word 返工在首次生图前必须创建并通过审核。

```json
{
  "schema_version": "1.0",
  "status": "reviewed",
  "scope": {
    "current_project": ".",
    "historical_packages": ["/absolute/path/to/previous-delivery"],
    "requested_operations": ["resegment", "reuse_frames", "word_rebuild"]
  },
  "libraries": {
    "avatar_library": "library/avatar_library.json",
    "product_library": "library/product_library.json",
    "product_bible": "library/product_bible.json",
    "knowledge_index": "library/knowledge_index.json"
  },
  "inventory": [
    {
      "asset_id": "FRAME-OLD-S001-F01",
      "asset_type": "approved_frame",
      "library_layer": "scene_shot",
      "path": "/absolute/path/to/frame.png",
      "sha256": "<sha256>",
      "width": 1080,
      "height": 1920,
      "approval_status": "approved",
      "rights_status": "cleared",
      "source_project": "previous-project",
      "source_shot_ids": ["S001"],
      "avatar_ids": ["AV-001"],
      "product_ids": ["PR-001"],
      "product_states": ["未开封零售外盒"],
      "has_source_subtitles": false,
      "has_watermark": false,
      "is_composite_or_contact_sheet": false
    }
  ],
  "shot_decisions": [
    {
      "shot_id": "S001",
      "decision": "reuse",
      "candidate_asset_ids": ["FRAME-OLD-S001-F01"],
      "selected_asset_ids": ["FRAME-OLD-S001-F01"],
      "required_avatar_ids": ["AV-001"],
      "required_product_ids": ["PR-001"],
      "required_product_states": ["未开封零售外盒"],
      "identity_review": "matched_and_authorized",
      "product_review": "matched",
      "scene_action_review": "matched",
      "allowed_deterministic_transforms": ["center_crop_to_9_16"],
      "generation_reason": null,
      "candidate_rejection_reasons": []
    }
  ],
  "summary": {
    "reused_frame_count": 1,
    "new_generation_count": 0,
    "rejected_asset_count": 0,
    "expected_word_image_count": 1
  }
}
```

`asset_type` 使用 `avatar_reference`、`face_approved_frame`、`product_reference`、`product_approved_frame`、`source_frame`、`beauty_or_action_candidate`、`approved_frame`、`word_extracted_frame` 或 `generated_result`。`library_layer` 使用 `avatar_identity`、`product_packaging`、`scene_shot` 或 `delivery`。

`decision` 使用 `reuse`、`new_generation` 或 `omit`。`reuse` 的 `selected_asset_ids` 必须存在、可访问、已批准且与人物授权和目标产品匹配；进入 Word 的画面必须是独立9:16、无源字幕水印、非拼图。`new_generation` 必须列出已经考虑的候选资产、逐项拒收原因和可观察的 `generation_reason`；目录变化、S 编号变化、重新拆镜或追求更美观不是合法理由。

人物与产品同时替换时，`required_avatar_ids` 与 `required_product_ids` 都必须非空，并分别通过身份/授权审核和产品/状态审核。不得用一项通过代替另一项。美观/动作候选帧只有在记录用户授权、提升后的新资产 ID 和同等级 QA 结论后，才能把 `approval_status` 改为 `approved`。

## 图文 Word 闭环

完成 `pipeline.py compile` 后，Word 导出必须读取同一批 `prompts/generation_pack.json` 和 `prompts/<shot-id>.md`，而不是聊天摘要。每镜要求：

- `approved_generation_first_frame` 是可访问文件；
- `product_references` 非空；
- Prompt 去除空白后为 3000–4000 字符；
- 目标人脸启用时，`edit_chain.face_edit_enabled=true`、`face_reference_ids` 非空且 `avatar_reference` 已获授权；
- 导出 `exports/*.docx` 及同名 `.manifest.json`，然后逐页渲染审核。
