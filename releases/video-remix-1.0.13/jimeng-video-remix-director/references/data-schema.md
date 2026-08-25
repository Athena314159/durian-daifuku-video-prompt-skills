# 数据结构

所有 JSON 使用 UTF-8、两空格缩进。未知值用 `null` 或 `unknown`，不要编造。

## project.json

`speech_strategy` 默认 `adaptive_from_script_and_source`。`allow_voiceover` 和 `allow_on_screen_speech` 表示允许范围，不代表实际镜头方式；实际选择保存在每镜 `audio.delivery_mode`。

阶段与产品模式必须显式保存：

```json
{
  "execution_tier": "source_intake",
  "product_mode": "preserve_source_product",
  "source_ready": false,
  "pending_inputs": ["revised_script"]
}
```

`execution_tier` 可用 `source_intake|diagnose_only|first_frame_only|prompt_only|full_delivery`。用户未明确换品时 `product_mode` 必须为 `preserve_source_product`，不得要求目标产品参考；只有明确换品才用 `replace_product`。`pending_inputs` 是下一阶段输入，不等于失败，也不降低已经完成的 `source_inventory_ready` 或 `transcript_ready`。

Source intake 分支统一使用 `source-intake-handoff-v1.0`。文线必须包含 `transcript.source_language`、连续可编辑 `transcript.editable_text`、带秒数的 `segments[]`、语言证据和 `controller_reply`；图线必须包含 `source_inventory.source_shots[]`。语言证据不得使用产品名、品牌名、国名或产地名。完整可执行 schema 见 `references/schemas/source_intake_handoff.schema.json`。

当前图文双 Agent 的默认交付规则还必须包含：

```json
{
  "prompt_length_contract": {
    "enabled": false,
    "minimum_non_whitespace_characters": 3000,
    "maximum_non_whitespace_characters": 4000
  },
  "project_rules": {
  "preserve_every_source_shot": true,
  "require_at_least_one_approved_image_per_source_shot": true,
  "require_at_least_one_approved_image_per_inserted_shot": true,
  "require_revised_script_full_coverage": true,
  "require_structured_six_layer_evidence": true,
  "require_frame_accurate_source_timeline": true,
  "minimum_generation_clip_seconds": 4.0,
  "merge_short_adjacent_source_shots": true,
  "minimum_eating_occurrences_when_source_duration_gte_30": 3,
  "insert_only_missing_eating_occurrences": true,
  "inserted_eating_occurrences_must_be_non_contiguous": true,
  "allow_immediate_speech_after_bite": true,
  "require_visible_swallow_or_post_bite_reaction": false,
  "package_artwork_policy": "preserve_master_projection",
  "user_delivery_format": "docx_only"
  }
}
```

`prompt_length_contract.enabled=false` 表示上下限都关闭；`true` 表示上下限同时成为硬门。pipeline、DOCX exporter 和 aligner 只能读取这一处，不接受聊天里临时口径或导出命令的另一套默认值。

`minimum_generation_clip_seconds` 约束的是送入即梦的一段连续生成片段，不是删除原片短分镜的理由。短于4秒的原片分镜仍作为原子 `source_shot` 保存，只能与时间上相邻的原子分镜合并。

## source/source_manifest.json 的原子分镜

看完原视频后先建立完整清单：

```json
{
  "duration": 35.2,
  "frame_rate": "30000/1001",
  "source_shots": [
    {
      "id": "SRC001",
      "start_frame": 0,
      "end_frame": 51,
      "timecode": {"start": 0.0, "end": 1.7, "duration": 1.7},
      "storyboard_description": "原片中可见的人物、动作、产品状态、构图和节奏"
    }
  ]
}
```

每个硬切、动作功能切换或原片独立分镜都必须出现一次。第一项从0开始，相邻边界无空洞/重叠，最后一项结束于 ffprobe `duration`，所有边界误差不超过半个源帧；`end_frame` 使用不包含该帧的 exclusive 语义。未知细节写 `unknown`，不能为了凑4秒省略、重写或合并清单项。

## planning/story_plan.json

```json
{
  "schema_version": "1.1",
  "status": "reviewed",
  "subtitle_script": {
    "provided_by_user": true,
    "path": "source/subtitle.txt",
    "text": "用户字幕全文",
    "effective_characters": 6,
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
    "minimum_generation_clip_seconds": 4.0,
    "maximum_single_shot_seconds": 8.0,
    "maximum_on_screen_chars_per_second": 5.0,
    "maximum_voiceover_chars_per_second": 5.5,
    "rhythm_notes": []
  },
  "eating_plan": {
    "source_duration_seconds": 35.2,
    "source_eating_occurrence_count": 1,
    "inserted_eating_occurrence_count": 2,
    "target_eating_occurrence_count": 3,
    "occurrences": [
      {
        "id": "E001",
        "shot_id": "S003",
        "source_shot_id": "SRC004",
        "origin": "source",
        "generation_timecode": {"start": 0.4, "end": 1.5, "duration": 1.1},
        "rhythm_rationale": "承接卖点后给出一次独立吃食证据，与其余吃食点分开",
        "source_evidence": ["张口、牙齿接触、产品离嘴、形成咬口"],
        "insertion_rationale": null,
        "visible_swallow_required": false,
        "speech_after_bite": {"enabled": true, "start_trigger": "product_left_mouth", "mouth_speakable_evidence": "咬合结束、产品离嘴、嘴唇与下颌恢复可说状态"},
        "appetite_evidence": {
          "bite_readability": "咬合点清楚",
          "crisp_sound": "短促咔嚓与沙沙",
          "product_state_change": "同一根产品缩短并出现咬口",
          "source_performance_basis": "继承原片送入口、眼神和头部节奏"
        }
      }
    ]
  },
  "break_plan": {
    "source_break_occurrence_count": 0,
    "inserted_break_occurrence_count": 2,
    "require_hands_only_product_showcase": true,
    "occurrences": [
      {
        "id": "B001",
        "shot_id": "S006",
        "inserted_shot_id": "ADD006",
        "mode": "hands_only_product",
        "origin": "inserted",
        "generation_timecode": {"start": 1.1, "end": 2.1, "duration": 1.0},
        "rhythm_rationale": "在材质口播重音处用纯手部断裂证明酥脆",
        "insertion_rationale": "目标产品与原产品不同，按原片展示停顿补入酥脆证据",
        "crisp_proof": {
          "single_snap": true,
          "fracture_visible": true,
          "material_conservation_locked": true,
          "crumbs": {"minimum": 3, "maximum": 8},
          "foley": "一次短促清楚的咔嚓和少量碎屑声",
          "complementary_orange_gold_fracture": "两个断面来自同一断点，橙金至焦糖橙且轮廓互补",
          "same_stick_two_piece_conservation": "断后仅两段，长度之和与断前同一根一致",
          "sound_sync": "咔嚓落在断裂出现的同一帧，碎屑声随下落结束"
        }
      }
    ]
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
      "merge_reason": "SRC001与SRC002均短于4秒，按原顺序合并为连续生成片段，不删镜",
      "source_units": [
        {
          "source_shot_id": "SRC001",
          "source_timecode": {"start": 0.0, "end": 1.7, "duration": 1.7},
          "generation_timecode": {"start": 0.0, "end": 1.7, "duration": 1.7},
          "storyboard_description": "按照原片节奏和新版口播推算后的可见分镜描述",
          "script_text": "这一原片分镜承担的新版口播原文",
          "source_first_frame": "source/shot_frames/SRC001/source_first_frame.jpg",
          "delivery_asset_ids": ["FRAME-SRC001-APPROVED"],
          "source_performance_layers": {
            "emotion_trigger": {"status": "observed", "source_timecode": {"start": 0.0, "end": 0.4, "duration": 0.4}, "source_reference_frame": "source/shot_frames/SRC001/evidence-emotion.jpg", "observable_evidence": "听到卖点后眉峰轻抬", "confidence": 0.94, "gap_reason": null},
            "gaze": {"status": "observed", "source_timecode": {"start": 0.1, "end": 0.7, "duration": 0.6}, "source_reference_frame": "source/shot_frames/SRC001/evidence-gaze.jpg", "observable_evidence": "视线从产品转向镜头", "confidence": 0.96, "gap_reason": null},
            "facial_microreaction": {"status": "not_visible", "source_timecode": null, "source_reference_frame": null, "observable_evidence": "人物背脸，嘴角和眉眼不可见", "confidence": 0.99, "gap_reason": null},
            "body_hand_preparation": {"status": "observed", "source_timecode": {"start": 0.0, "end": 1.2, "duration": 1.2}, "source_reference_frame": "source/shot_frames/SRC001/evidence-hand.jpg", "observable_evidence": "右手沿原路径抬起产品，左手保持占用", "confidence": 0.98, "gap_reason": null},
            "breath_pause": {"status": "audible", "source_timecode": {"start": 1.2, "end": 1.4, "duration": 0.2}, "source_reference_frame": null, "observable_evidence": "动作重拍前存在短促停顿", "confidence": 0.82, "gap_reason": null},
            "voice_speech": {"status": "not_applicable", "source_timecode": null, "source_reference_frame": null, "observable_evidence": "本单元由画外音覆盖，人物没有屏内口播", "confidence": 1.0, "gap_reason": null}
          }
        },
        {
          "source_shot_id": "SRC002",
          "source_timecode": {"start": 1.7, "end": 4.0, "duration": 2.3},
          "generation_timecode": {"start": 1.7, "end": 4.0, "duration": 2.3},
          "storyboard_description": "第二个原片分镜继续原动作与新版口播",
          "script_text": "第二段新版口播原文",
          "source_first_frame": "source/shot_frames/SRC002/source_first_frame.jpg",
          "delivery_asset_ids": ["FRAME-SRC002-APPROVED"]
        }
      ],
      "inserted_units": [],
      "purpose": "叙事或转化目的",
      "source_facts": [],
      "source_locks": [],
      "allowed_changes": ["只替换食物主体"],
      "scene": {"location": "场景", "background": [], "foreground": []},
      "character": {
        "present": true,
        "hands_only": false,
        "identity": "人物描述",
        "position": "位置",
        "gaze": "视线",
        "micro_expressions": []
      },
      "emotion": {
        "start": "起始情绪",
        "trigger": "来自原片可见/可听事实的情绪触发",
        "inferred_intention": "人物此刻想完成什么；属于有证据的导演推断",
        "progression": [],
        "end": "结束情绪",
        "narrative_payoff": "这个表演转折最终让观众感受到什么并如何承接下一拍",
        "evidence_basis": ["源时间、视线、动作、表情或声音证据"],
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
        "profile": "durian-daifuku-v2",
        "state": "opening_window_seed",
        "count": "1",
        "packaging": "none",
        "shot_specific_traits": [],
        "scale_lock": {
          "mode": "physical_consistency",
          "source_scale_role": "pose_only_incompatible_scale",
          "anchor": {
            "type": "index_finger_mid",
            "expected_ratio": [3.5, 4.0],
            "evidence": "产品与接触食指同景深，完整可重建宽度按食指中段复核"
          }
        },
        "surface_lock": {
          "rice_flour_haze": true,
          "visible_in_oblique_light": true,
          "individually_resolvable_particles": false
        },
        "filling_lock": {
          "continuous_puree_ratio": 0.9,
          "countable_lumps": false,
          "holes_or_honeycomb": false,
          "stringing": false
        },
        "endpoint_lock": {
          "terminal_state": "opening_window_seed",
          "single_endpoint": true,
          "max_visible_filling_area_ratio": 0.05,
          "piece_air_gap_cm": 0
        },
        "reference_roles": [
          {
            "asset_id": "DF2-OPENING-SEED-01",
            "role": "opening_topology_only",
            "allowed_inheritance": ["双手初始受力位置", "首次微露馅的时间状态"],
            "forbidden_inheritance": ["产品大小", "扁平轮廓", "手和指甲身份", "背景产品", "光线", "规则孔形"]
          }
        ],
        "package_artwork": {
          "artwork_scaled_or_relaid_out": false,
          "visible_faces": [
            {
              "box_id": "BOX001",
              "face": "front",
              "visibility_state": "occluded",
              "visible_extent": "partial",
              "master_reference": "source/references/package-front-master.png",
              "expected_visible_regions": ["左上品牌", "中央主标题"],
              "expected_visible_polygon": [[20, 40], [820, 40], [820, 840], [20, 840]],
              "visible_area_ratio": 0.42,
              "legibility_required": true,
              "occluded_or_offframe_regions": ["右下产品图自然出框"],
              "natural_crop_or_occlusion": true,
              "projection_method": "homography",
              "qa_evidence": {
                "candidate_face_crop": "review/package/S001-front-crop.png",
                "candidate_face_crop_sha256": "<真实SHA-256>",
                "delivery_asset_id": "FRAME-SRC001-APPROVED",
                "parent_image_sha256": "<DOCX正文批准图真实SHA-256>",
                "crop_rect_xywh": [20, 40, 800, 800],
                "master_sha256": "<母版真实SHA-256>",
                "projection_manifest": "review/package/S001-front.projection.json",
                "projection_manifest_sha256": "<投影manifest真实SHA-256>",
                "visible_region_checkpoints": [
                  {"id": "左上品牌", "status": "matched"},
                  {"id": "中央主标题", "status": "matched"}
                ],
                "text_legibility": "matched",
                "orientation": "matched",
                "cross_edge_registration": "not_applicable",
                "cross_edge_registration_reason": "该机位没有可见跨棱印刷",
                "occlusion_scope": "matched",
                "model_redraw_detected": false,
                "unexpected_missing_region": false
              },
              "qa_status": "approved"
            }
          ]
        }
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
          "atomic_identity_product_required": false,
          "retry_origin_policy": "exact_original_source_only",
          "partial_candidate_policy": "diagnostic_only_never_reuse",
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

六个 key 固定为 `emotion_trigger`、`gaze`、`facial_microreaction`、`body_hand_preparation`、`breath_pause`、`voice_speech`。状态只允许 `observed|audible|not_visible|not_applicable|template_supplement`。不可见/不适用必须说明事实原因；模板补全必须写 `gap_reason`，不得冒充原片识别。每个适用的人物/手部镜至少有一层真实 `observed` 或 `audible`。

每只 `box_id` 都必须各有 `front/side/top` 三行；完全看不见也写 `visibility_state=hidden`、`visible_extent=none` 与真实遮挡/出框原因。包装 `qa_status=approved` 不能单独放行。每个可见或部分遮挡面必须附原尺寸候选裁切、绑定 DOCX 正文批准图的 asset id/父图哈希/裁切坐标、母版真实哈希、`project_package_master.py` 产生的投影 manifest 路径与真实哈希、逐个可见图案/文字检查点，以及文字、方向、跨棱登记、遮挡范围、模型重绘和意外缺块结论。投影 manifest 内 candidate/master/output 哈希、目标四边形、遮挡 mask、`projection_method=homography` 与 `model_redraw_used=false` 必须和本次成品一致；任何缺项、额外项、非 `matched`、哈希不一致或只写“已通过”没有证据，均阻断该整张图进入 Word。

`source_units` 只允许保存原片 `SRC…`；按吃食计数、黄油脆丝棒纯手掰开或用户明确要求补入的新镜头写入 `inserted_units`，使用独立 `ADD…`，不得虚构原片时间：

```json
{
  "id": "S006",
  "timecode": {"start": 20.0, "end": 24.0, "duration": 4.0},
  "source_units": [],
  "inserted_units": [
    {
      "inserted_shot_id": "ADD006",
      "generation_timecode": {"start": 0.0, "end": 4.0, "duration": 4.0},
      "storyboard_description": "按原片停顿节奏与新版口播材质重音新增纯手掰开镜头",
      "script_text": "听这个咔嚓声，就知道有多酥脆。",
      "delivery_asset_ids": ["FRAME-ADD006-APPROVED"],
      "insertion_rationale": "黄油脆丝棒要求至少一个无人出镜的纯手掰开证明镜头",
      "rhythm_anchor": "材质卖点句重音后停半拍",
      "source_reference_shot_ids": ["SRC007", "SRC008"],
      "source_reference_frame": "source/shot_frames/SRC007/beauty_candidate_03.jpg",
      "source_performance_layers": "同 source_units 的完整六层对象"
    }
  ]
}
```

一个 `S` 也可同时包含两类 unit。总控把它们按 `generation_timecode.start` 合并检查，必须从0.00连续覆盖整个 `S`，不得有空洞或重叠；`source_units` 内的 SRC 仍须保持原片全集、顺序和唯一性。新增吃食/掰开 occurrence 的 `inserted_shot_id` 必须命中同一 `S` 内的 ADD；原片 occurrence 的 `source_shot_id(s)` 必须命中同一 `S` 内的 SRC。

每个普通 SRC/ADD 的 `delivery_asset_ids` 必须是至少含1项的有序数组，资产必须独立、已批准、可访问且 provenance 明确绑定本 unit。同一 unit 的动作跨越多个关键状态时可保留多张图，并用 `delivery_asset_roles` 逐个说明不可互换的动作职责；多图仍只算一个吃食/掰开事件，不能把三张状态图误算成三次事件。不同 unit 默认不得复用同一 asset ID、解析后路径或实际 SHA-256。

相邻 `S` 为保持连续性而重复显示上一/下一段 owner unit 的图时，只能写成显式边界参考；caption 与 manifest 仍绑定真实 owner，且该图不计入当前 unit 的最低覆盖：

```json
{
  "continuity_boundary_references": [
    {
      "owner_unit_id": "SRC008",
      "asset_id": "FRAME-SRC008-ACTION-02",
      "responsibility": "承接上一段末态，锁定下一段开场手位与产品断面",
      "continuity_boundary_reference": true
    }
  ]
}
```

`subtitle_script.effective_characters` 由程序只计汉字、字母和数字。依生成时间拼接全部 `source_units[].script_text + inserted_units[].script_text`，以及依 S 顺序拼接全部有声 `audio.script_text`，两者都必须与锁定 `subtitle_script.text` 等价；任何删字、重复、用图片截图替代可编辑口播或手填虚假字数均阻断。

口播有效字符只计汉字、字母和数字。`speakable_seconds` 必须扣除屏内人物入口、实际咬合、实际存在的闭口咀嚼、必要换气、纯拟音和无声观察；只有原片确实出现吞咽时才扣除吞咽。人物在咬合完成、产品离嘴后可以按原片节奏马上说新版口播，不需要先补一个不存在的吞咽或吃后反应。`characters_per_second` 必须等于有效字符数除以实际可说时段。默认每镜不超过3个台词句段，连续生成片段通常为4–8秒，屏内口播不超过5.0字/秒，画外音不超过5.5字/秒。Prompt字符下限不得成为合并镜头的理由。

产品展示镜头使用 `visual_type=product_showcase`，并设置 `character.present=false`。纯产品静物令 `character.hands_only=false`；硬性纯手部掰开镜令 `character.hands_only=true`，只允许动作所需的双手与产品，不出现脸、头、躯干或人物身份。其他两类镜头必须设置 `character.present=true` 并填写人物与情绪。

## 包装母版

产品规范中的 `package_artwork` 必须声明：

```json
{
  "policy": "preserve_master_projection",
  "face_masters": {
    "front": "source/references/package-front-master.png",
    "side": "source/references/package-side-master.png",
    "top": "source/references/package-top-master.png"
  }
}
```

只审核实际可见区域。自然出框、手部遮挡、前景遮挡可以记录为 `occluded_or_offframe_regions`；它们不算缺图。实际可见的文字或图案缺失、乱码、镜像、跨折边错位、被压缩或为露全而重排，才是阻断。可读包装印刷优先用母版单应性/确定性合成，生成模型只负责纸盒几何、折边、光照、反光、接触影和边缘融合。

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
      "approval_status": "user_approved",
      "user_approval": {
        "status": "user_approved",
        "display_receipt_id": "gallery-20260824-001",
        "approved_at": "2026-08-24T12:01:00+08:00",
        "asset_sha256": "<sha256>"
      },
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
  "gallery_receipt": {
    "status": "user_approved",
    "display_receipt_id": "gallery-20260824-001",
    "displayed_at": "2026-08-24T12:00:00+08:00",
    "approved_at": "2026-08-24T12:01:00+08:00",
    "asset_refs": [{"shot_id": "S001", "asset_id": "FRAME-OLD-S001-F01", "sha256": "<sha256>"}]
  },
  "summary": {
    "reused_frame_count": 1,
    "new_generation_count": 0,
    "rejected_asset_count": 0,
    "expected_word_image_count": 1
  }
}
```

`asset_type` 使用 `avatar_reference`、`face_approved_frame`、`product_reference`、`product_approved_frame`、`source_frame`、`beauty_or_action_candidate`、`approved_frame`、`word_extracted_frame` 或 `generated_result`。`library_layer` 使用 `avatar_identity`、`product_packaging`、`scene_shot` 或 `delivery`。进入某个 SRC 的每张交付图必须在 inventory 的 `source_shot_ids` 明确包含该 SRC；进入某个 ADD 的每张交付图必须在 `inserted_shot_ids` 或 `storyboard_unit_ids` 明确包含该 ADD。不得依靠文件名猜归属。`shot_decisions[].selected_asset_ids` 必须等于该 S 所有 unit 按生成顺序拼接后的完整 `delivery_asset_ids`，不能漏掉多状态图或夹入无 owner 的图。

每个被选中的可交付资产还必须保存 `origin_bundle_release_id` 与 `origin_product_profile`，并与当前项目 `contract_binding` 一致。缺失来源、旧 release、旧产品合同或迁移前批准图一律不能直接复用；新生成资产还需指向匹配图片 SHA-256 的 `image_generation_result_receipt`。

`decision` 使用 `reuse`、`new_generation` 或 `omit`。`reuse` 的 `selected_asset_ids` 必须存在、可访问、已批准且与人物授权和目标产品匹配；进入 Word 的画面必须是独立9:16、无源字幕水印、非拼图。`new_generation` 必须列出已经考虑的候选资产、逐项拒收原因和可观察的 `generation_reason`；目录变化、S 编号变化、重新拆镜或追求更美观不是合法理由。

人物与产品同时替换时，`required_avatar_ids` 与 `required_product_ids` 都必须非空，并设置 `atomic_identity_product_required=true`、`retry_origin_policy=exact_original_source_only`、`partial_candidate_policy=diagnostic_only_never_reuse`。两项必须在同一个授权请求和同一候选图上联合通过，不得用一项通过代替另一项，也不得拿半成品继续补改。美观/动作候选帧先通过结构与视觉 QA，仍只是视觉已审；总控完整展示按 unit 排序的单图总览并取得用户确认后，才能把 `approval_status` 改为 `user_approved`，并写入与图片 SHA-256 绑定的 `user_approval` 与全量 `gallery_receipt`。

## 图文 Word 闭环

完成 `pipeline.py compile` 后，Word 导出必须读取同一批 `prompts/generation_pack.json`、`prompts/history/<compile_id>/input_snapshot.json` 和 `prompts/<shot-id>.md`，而不是聊天摘要。generation pack 必须记录 canonical input hashes、项目唯一的 `prompt_length_contract`、每个 S 的完整 `source_units` 与 `inserted_units`、Prompt 哈希/文件哈希/实算字符数；任何 canonical 输入或 Prompt 在 compile 后变化都必须先重编译，不得混用旧 pack。每镜要求：

- `approved_generation_first_frame` 是可访问文件；
- `product_references` 非空；
- 只读取 `project.json.prompt_length_contract`：`enabled=true` 时上下限同时为硬门，`enabled=false` 时上下限同时关闭；不得再用导出命令参数建立第二套长度事实源；
- 目标人脸启用时，`edit_chain.face_edit_enabled=true`、`face_reference_ids` 非空且 `avatar_reference` 已获授权；
- 导出用户侧唯一 `*.docx`，把 manifest 写入项目内部 `review/`，然后逐页渲染审核。
