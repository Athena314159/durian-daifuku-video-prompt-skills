# 输出契约、角色锁与阻断码

只读取和执行当前交付档位需要的部分。`source_intake` 直接展示用户必须修改的原口播正文、时间码核对稿和可点击已核验源图；`prompt_only` 不要求 DOCX/manifest；`first_frame_only` 不要求 TXT/DOCX；`full_delivery` 的用户侧最终交付固定为一个原生可编辑 DOCX。总 TXT、逐镜 TXT、JSON、manifest、Markdown、审核表、渲染图和图片清单是内部事实源，不属于用户最终交付。原口播正文与图线各阶段可点击图库不得被“内部文件”规则隐藏在 JSON 路径后面；图库是任务内阶段进度，不写入最终用户输出目录。未完成 QA 的图片必须标“候选、未批准”，批准状态不能靠预览或 handoff 自报。

## 1. 内部构建目录与用户交付目录

使用 UTF-8 输出；总文件与逐镜文件的同一镜头必须逐字一致。

```text
<project-directory>/
├── work/
│   ├── 完整逐分镜Prompt.txt
│   ├── 人物角色锁定表.txt
│   ├── 内容语义审核报告.txt
│   ├── 即梦逐镜视频Prompt.manifest.json
│   ├── planning/
│   │   ├── role_lock.json
│   │   ├── story_plan.json
│   │   ├── text_handoff.json
│   │   └── asset_reuse_plan.json
│   └── shots/
│       ├── S001_标题摘要.txt
│       ├── S002_标题摘要.txt
│       └── ...
└── outputs/
    └── 即梦逐镜视频Prompt.docx
```

标题优先取本镜口播的核心内容；无口播时用一句简短动作摘要。文件名去除路径非法字符。

`asset_reuse_plan.json` 位于人物/换脸库、产品/包装库、场景分镜库与交付层之间。Manifest 必须记录复用帧数、补生帧数、预期 Word 图片数、每张图片的资产 ID、旧/新 S 编号、路径、哈希、尺寸、avatar/product 绑定和补生理由。不得只在聊天中口头说明复用关系。

`full_delivery` 的项目最终回复只提供 DOCX 链接与一句验收摘要，不附内部表格或逐项列出内部文件。`outputs/` 中必须恰好有一个 `.docx`；出现 TXT、JSON、Markdown、CSV、图片、manifest、审核表或对齐表时触发 `USER_DELIVERY_ARTIFACT_LEAK`。此前在任务正文内显示的 source-intake 可点击图库不属于 `outputs/` 文件泄漏，也不得在最终回复重复成额外交付包。

## 2. 原口播交接

完整 Prompt 前先在对话中提交两份文本。

### 纯净可编辑稿

```text
【原片口播/字幕｜请直接修改后发回】

说话人A：第一段原口播……
说话人A：第二段原口播……
说话人B：第三段原口播……
```

不要插入时间码、分析括号或产品建议。只有画面字幕而没有可听语音时，标题改为 `【原片画面字幕｜请直接修改后发回】`。

### 带时间码核对稿

```text
00:00.000–00:03.200｜说话人A｜屏内对白（初判）
听到的原口播：……
画面原字幕：……
差异：一致 / 字幕省略了…… / [听不清 00:02.840]
证据：可见口型同步 / 镜外声源 / 暂不能确认
```

语音与画面字幕分别保存，不允许互相静默覆盖。用户发回修改稿后保存新版口播，原稿继续作为证据。

### Source-intake 可点击首帧图库

图任务与总控都直接展示图片本身，不以“文件已生成”代替用户可见交付：

1. 首张源图通过解码、准确 PTS、尺寸和画面事实核验后，立即在 commentary 使用绝对本地路径嵌入可点击图片：`[![SRC001｜00:00.000｜已核验源图](</绝对路径/SRC001.png>)](</绝对路径/SRC001.png>)`。纯路径、代码块、JSON、handoff 链接或进度句均不合格。
2. 在约 25%/50%/75%/100% 完成度跨点更新。每次只展示上次以后新增的已核验图片；数量较多时可发一张可点击联系表。每个更新都必须带图，联系表不能替代最终逐张图库。
3. 阶段最终回复按 `SRC`、再按合同确实存在的 `ADD` 稳定排序，为每个编号逐张嵌入可点击本地图片并标注准确时间。source intake 不创建 ADD，因此通常只列全部 SRC；禁止以路径清单、单张总拼图或 JSON 代替。
4. 总控一收到并校验 image-ready handoff，就在当前用户任务镜像同一图库，不等待文线、口播修改、目标产品、目标人物或授权。缺这些下一阶段输入不得写成 source-intake blocker；只有图片缺失、不可读或 handoff 验证失败才退回图线。
5. 把这些图片保留为任务内阶段进度；进入 `full_delivery` 后仍只把唯一 DOCX 作为项目最终交付。

### Full-delivery 换品/生成图进度

1. 首张换品或生成图落盘后即可在图任务直接展示；QA 尚未跑完时，图片标题和紧邻文字必须写“候选、未批准”，不得称为完成图、批准帧或可交付图。
2. 约 25%/50%/75%/100% 跨点必须带本轮新增图片或可点击联系表；路径、JSON、百分比文字和“继续跑任务”按钮都不能代替图片。
3. image-ready 前先按 locked shot map 的 `SRC`、再按 `ADD` 顺序嵌入每个单元自己的全部视觉 QA 通过图；每个单元至少一张，同一单元可有多张有序动作状态图。联系表只能辅助预览，不能替代逐卡图片；跨单元复用不能冒充另一个单元的交付图。此时统一标“视觉已审、待用户确认”。
4. 总控校验后立即在当前任务镜像完整图库并取得用户明确确认，记录绑定全量顺序与图片 SHA-256 的 `gallery_receipt`；每张图片再提升为 `user_approved`。只有该状态可以 `ready_for_merge` 并进入 Word。图片是任务内进度，最终项目文件仍只有唯一 DOCX。

## 3. `planning/role_lock.json`

在完整 Prompt 之前建立角色锁。不可变键 A/B/C 不因用户称呼变化而改变。

```json
{
  "characters": {
    "A": {
      "label": "女生",
      "visibility": "offscreen_all",
      "camera_holder": true,
      "relations": ["收礼者", "被喂食者"],
      "evidence": ["用户明确纠正：女生全程没有出现在镜头里"]
    },
    "B": {
      "label": "男生",
      "visibility": "onscreen_all",
      "camera_holder": false,
      "relations": ["送礼者", "喂食者"],
      "evidence": ["原片画面持续可见；与镜头后人物对视"]
    }
  },
  "spatial_invariants": [
    "女生全程位于镜头后，不出现脸、嘴、身体、影子、倒影或自拍画面",
    "男生与女生互动时看向镜头后的女生眼睛位置"
  ],
  "speech_plan": {
    "source": "creative_proposal",
    "disclosed_to_user": true,
    "summary": "轻微川渝年轻情侣城市口音；女声语速中快、惊喜时尾音上扬并带笑气；男声略慢半拍、尾音短收；平翘舌不刻意咬正，不夸张模仿方言"
  },
  "dialogue": [
    {
      "speaker_id": "A",
      "label": "女生",
      "text": "今天七夕几点了才回来？",
      "delivery": "offscreen_live",
      "visible_lip_sync": false,
      "action_subject": "男生",
      "visual_evidence": "男生听到质问后抬眼看向镜头后的女生"
    }
  ],
  "required_actions": []
}
```

`visibility` 使用：

- `offscreen_all`：全程镜头后或镜外；不得生成该人物任何可见身体信息；
- `onscreen_all`：全程画面中；
- `partial`：按分镜另行列出可见范围。

当原片含吃或咬动作时，不再用固定 `bite_chain` 强制补齐吞咽和吃后反应。把原片逐帧可见阶段写入 `planning/story_plan.json` 的 `eating_plan.occurrences[].required_phases`；自动检查只要求 Prompt 复刻被记录的阶段。`swallow` 和 `post_eating_reaction` 只有原片确实可见或用户明确新增时才能列为必选。咬合与闭口咀嚼期间仍禁止吃食者屏内完整口播；该占用动作一结束，即可在下一动作段马上说新版口播。

## 3.1 `planning/story_plan.json`

`story_plan.json` 是原分镜覆盖、生成镜合并、分镜描述和全片吃食节奏的机器事实源。最小结构：

下列示例是文案策划阶段的可读 authoring 结构，不可直接冒充 full-delivery v2 handoff 或 `locked_shot_map.json`。总控在派发 full delivery 前必须把它确定性归一化成 [schemas/text_handoff.schema.json](schemas/text_handoff.schema.json) 使用的 v2 六项语义载荷：生成镜改用连续全局 `generation_timecode` 与有序 `unit_ids`；吃食阶段改用 `approach / bite / fracture / withdraw / closed_mouth_chew / speech_transition`，并补齐独立 `event_group_id / unit_id / timeline_timecode / script_anchor / non_contiguous_event=true`；每个 SRC/ADD 补齐六层与 `packaging_evidence`。没有完成该归一化就不得生成 `text-handoff-v2.0`。

```json
{
  "source_duration_seconds": 32.4,
  "generation_time_policy": {
    "min_duration_seconds": 4.0,
    "max_duration_seconds": 12.0,
    "onscreen_speech_max_chars_per_second": 5.0,
    "voiceover_max_chars_per_second": 5.5
  },
  "prompt_length_contract": {
    "enabled": false,
    "minimum_non_whitespace_characters": 3000,
    "maximum_non_whitespace_characters": 4000
  },
  "source_shot_inventory": [
    {
      "source_shot_id": "SRC001",
      "source_start": "00:00.000",
      "source_end": "00:01.800",
      "duration_seconds": 1.8,
      "action": "人物转头看向桌上的产品",
      "source_first_frame": "work/source_frames/SRC001.png",
      "approved_delivery_image": "work/approved_frames/SRC001.png"
    }
  ],
  "generation_shot_map": [
    {
      "shot_id": "S001",
      "origin": "source_merge",
      "source_shot_ids": ["SRC001", "SRC002"],
      "generation_duration_seconds": 4.6,
      "shot_description": "按原片 SRC001→SRC002 的转头、拿起产品连续动作，把新版口播第一句落在拿稳之后",
      "revised_script_anchor": "新版口播第1句",
      "merge_reason": "两个相邻源分镜均不足4秒，空间与动作连续"
    },
    {
      "shot_id": "S008",
      "origin": "inserted_eating",
      "inserted_shot_ids": ["ADD008"],
      "source_shot_ids": [],
      "generation_duration_seconds": 4.0,
      "shot_description": "根据原片人物送入口节奏与新版口播口感转折新增一次非连续吃食证明",
      "revised_script_anchor": "新版口播第4句之后",
      "insertion_rationale": "原片达到30秒但吃食次数不足3次，只补缺少的一次",
      "rhythm_anchor": "口感卖点句后的切点",
      "source_reference_shot_ids": ["SRC004", "SRC005"],
      "source_reference_frame": "work/source_frames/SRC004.png"
    }
  ],
  "eating_plan": {
    "source_eating_occurrence_count": 1,
    "inserted_eating_occurrence_count": 2,
    "target_eating_occurrence_count": 3,
    "occurrences": [
      {
        "id": "EAT-S01",
        "origin": "source",
        "shot_id": "S003",
        "source_shot_ids": ["SRC004"],
        "generation_timecode": {"start": 0.6, "end": 1.8, "duration": 1.2},
        "narrative_section": "首次口感证明",
        "rhythm_rationale": "保留原片00:08.200动作停顿后的真实吃食切点",
        "revised_script_anchor": "新版口播口感句之前",
        "required_phases": ["approach", "bite_contact", "withdraw", "closed_chew"],
        "source_evidence": ["可见送入口、咬合、产品离嘴与闭口咀嚼"],
        "visible_swallow_required": false,
        "speech_after_bite": {"enabled": true, "start_trigger": "product_left_mouth", "mouth_speakable_evidence": "产品离嘴且嘴唇、下颌恢复可说状态"},
        "appetite_evidence": {"bite_readability": "咬合点清楚", "crisp_sound": "咔嚓与沙沙声同步", "product_state_change": "同一根缩短并形成咬口", "source_performance_basis": "继承原片入口、咀嚼、视线与手部节奏"}
      },
      {
        "id": "EAT-I01",
        "origin": "inserted",
        "shot_id": "S008",
        "inserted_shot_id": "ADD008",
        "generation_timecode": {"start": 0.8, "end": 2.0, "duration": 1.2},
        "narrative_section": "中段材质证明",
        "rhythm_rationale": "在新版口播从产品规格转入酥脆口感的语义切点置入",
        "revised_script_anchor": "新版口播第4句之后",
        "insertion_rationale": "原片达到30秒但真实吃食事件不足3次，只补缺少的节奏点",
        "required_phases": ["approach", "bite_contact", "crisp_fracture", "withdraw", "closed_chew"],
        "visible_swallow_required": false,
        "speech_after_bite": {"enabled": true, "start_trigger": "product_left_mouth", "mouth_speakable_evidence": "产品离嘴且嘴唇、下颌恢复可说状态"},
        "appetite_evidence": {"bite_readability": "咬合点清楚", "crisp_sound": "咔嚓与沙沙声同步", "product_state_change": "同一根缩短并形成咬口", "source_performance_basis": "只按相邻源片的入口、咀嚼和手部节奏补缺"}
      }
    ]
  }
}
```

字段规则：

- `source_shot_inventory` 必须覆盖原视频每个硬切/动作分镜且 ID 唯一；每项必须有绝对时间、动作、原始首帧和对应批准分镜图。
- `generation_shot_map` 必须覆盖每个源分镜且不得重复吞并；合并只能使用 inventory 中时间相邻、顺序不变的源 ID。不足4秒的源分镜必须与相邻可连续分镜合并，所得生成镜不少于4秒；禁止删镜。
- `origin` 以 `inserted` 开头的映射必须使用唯一 `inserted_shot_ids=["ADD…"]`，且 `source_shot_ids=[]`；同时保存新增理由、节奏锚点、源片参考 ID/帧。ADD 没有原片秒数，不能冒充 SRC，但仍必须做独立批准图并在 Word 生成可编辑分镜卡。
- `shot_description` 必须以所列源分镜的动作和节奏为底，并记录新版口播如何落位；不能只写营销标题。
- `origin=inserted` 的生成镜可以没有源 ID，但必须有节奏锚点和新版口播锚点；它不能冒充原片已有分镜。
- 原视频达到30秒时，`eating_plan` 必须声明实际 `source_eating_occurrence_count / inserted_eating_occurrence_count / target_eating_occurrence_count`，并满足 `target=max(source,3)`、`inserted=max(0,3-source)`、`len(occurrences)=target`。原片已有四次就保留四次，不能删成三次。只有一对相同或相邻生成镜中至少含一个 `origin=inserted` 时才阻断；两个原片真实事件即使相邻也必须保留。
- authoring 阶段可观察更细的 `open_mouth / bite_contact / crisp_fracture / closed_chew` 等动作，但正式 v2 handoff 的 `required_phases` 必须确定性归一化为 `approach / bite / fracture / withdraw / closed_mouth_chew / speech_transition`。吞咽和吃后反应只保留在原片证据/可编辑描述中，不能作为模板固定结尾；原片没有时不得强制加入。

### Full delivery 文任务交接字段与总控唯一事实源

本节只适用于 `execution_tier=full_delivery`。`source_intake` 先按导演 Skill 的 source-intake schema 直接交接可编辑原口播，不要求尚未锁定的 SRC/ADD/S。Full delivery 文任务不再交付自由文本摘要，而是交付一个 `schema_version=text-handoff-v2.0`、`execution_tier=full_delivery`、`branch_role=text` 的版本化 JSON。旧 `text-handoff-v1.0` 不再可合并；中文“文任务”只用于界面说明，不得写入 `branch_role`。权威参考是 [schemas/text_handoff.schema.json](schemas/text_handoff.schema.json)；可执行闸门 `scripts/validate_text_handoff.py` 直接复用 director 的 canonical v2 校验逻辑，避免两套 S/SRC/ADD 解释漂移。

顶层必填：

- 版本与分支：`schema_version / execution_tier / branch_role / source_duration_seconds`;
- 锁定与状态：`locked_semantic_hash / shot_map_sha256 / status / completed_shot_ids / completed_source_shot_ids / completed_inserted_shot_ids / blocked_items / artifacts`；两个 hash 必须相同；
- 完整集合：`collections.shot_ids / source_shot_ids / inserted_shot_ids / unit_ids`，逐项等于总控锁定的完整 S/SRC/ADD/混合单元顺序；
- 完整单元：`source_units / inserted_units`，不能只传 S 级摘要；
- 生成映射与节奏：`generation_shot_map / eating_plan / break_plan`。

每个 `source_units[]` 必须包含 `shot_id / source_shot_id / source_timecode / generation_timecode / storyboard_description / script_text / source_performance_layers / packaging_evidence`。每个 `inserted_units[]` 必须包含 `shot_id / inserted_shot_id / generation_timecode / storyboard_description / script_text / insertion_rationale / rhythm_anchor / source_reference_shot_ids / source_reference_frame / source_performance_layers / packaging_evidence`；ADD 不得编造 `source_timecode`。六层使用固定键 `emotion_trigger / gaze / facial_microreaction / body_hand_preparation / breath_pause / voice_speech`；每层显式写 `observed / audible / not_visible / not_applicable / template_supplement`，并携带源时间、参考帧、可观察证据、置信度和缺口原因，模板补充不得冒充源片事实。`packaging_evidence.visible=true` 时，每个实际可见的正面/侧面/顶面必须绑定批准母版资产 ID、绝对路径、真实 SHA-256、可见区域、观察证据和 `qa_status=approved`；自然裁切或遮挡允许，但不能省略实际可见面。

每个 `generation_shot_map[]` 必须包含 `shot_id / generation_timecode / unit_ids / source_shot_ids / inserted_shot_ids`。S 按完整生成时间轴从 `0.000` 连续覆盖到结尾；S 内每个 SRC/ADD 单元从镜内 `0.000` 起连续覆盖该 S，`unit_ids` 必须逐项等于同镜 SRC/ADD 的 canonical 顺序。一个单元只能有一个 S owner，不能靠标题、表格或聊天说明重新对齐。

`eating_plan.policy` 固定声明 `source_duration_threshold_seconds=30 / target_event_count=3 / events_are_non_contiguous=true / one_event_is_not_multiple_images=true`。每个 `occurrences[]` 必须携带独立 `id / event_group_id / shot_id / unit_id / origin / generation_timecode / timeline_timecode / rhythm_anchor / script_anchor / required_phases / non_contiguous_event=true`，并精确绑定一个 SRC 或 ADD。30秒以上视频保留全部 source 吃食事件，只插入足够的独立节奏事件补到3次；一个真实吃食事件即使拆成三张图、三行或三个动作阶段也仍只能计一次，禁止把“一个事件三张图”误当“三个吃食事件”。

`break_plan.occurrences[]` 必须传 `id / shot_id / unit_id / mode / origin / generation_timecode / rhythm_rationale / crisp_proof`；`origin=source` 用 `source_shot_id+source_evidence`，`origin=inserted` 用 `inserted_shot_id+insertion_rationale`。`crisp_proof` 硬性表达同一根、一次脆断、可见断点、橙金同色互补断面、3–8粒局部掉渣、两段守恒和“咔嚓”声与断裂帧同步；黄油脆丝棒完整交付必须同时出现 `mode=person_present` 与 `mode=hands_only_product` 的可观察事件，且每次各自绑定准确 unit。

`locked_semantic_hash` 与 `shot_map_sha256` 不是对聊天摘要或自由扩写稿求值。二者必须完全相同，并保持总控派工时那份只读 `locked_shot_map.json` 的摘要：只从锁文件取 `source_duration_seconds / source_units / inserted_units / generation_shot_map / eating_plan / break_plan`，按 UTF-8、键排序、无空白 JSON 规范化后求 SHA-256。v2 的文 handoff 必须逐项镜像这六项锁定载荷；若需改口播、分镜描述、六层、包装、吃食或掰开事实，先由总控产生新锁再重新派工，文分支不得在旧 hash 下静默改写。深层校验必须显式传入原锁，禁止从 handoff 自身猜回锁载荷。运行：

`python3 scripts/validate_text_handoff.py planning/text_handoff.json --locked-shot-map planning/locked_shot_map.json --print-shot-map-sha256`

以上交接载荷不是第二套可独立改写分镜的事实源。总控收到图、文两路交接后，必须按固定映射一次性归一化到导演项目：

- `collections` → 合并器预先锁定的完整 S/SRC/ADD/unit 集合与 canonical 顺序，任何缺失、重复或换序均停止合并；
- `source_units[].source_shot_id / source_timecode / storyboard_description / script_text / source_performance_layers / packaging_evidence` → `source/source_manifest.json.source_shots` 与最终逐 SRC 可编辑 Word 卡；
- 非新增 `generation_shot_map[].source_shot_ids` → `shots/shot_manifest.json.shots[].source_units[].source_shot_id`；
- 新增 `generation_shot_map[].inserted_shot_ids[]` → `shots/shot_manifest.json.shots[].inserted_units[].inserted_shot_id`，并逐 ADD 保留生成时间、描述、口播、六层、包装、新增理由、节奏锚点和源片参考；
- `eating_plan.occurrences` 原样归一化到 `planning/story_plan.json.eating_plan.occurrences`，source 事件绑定 SRC，inserted 事件绑定 ADD；
- `break_plan.occurrences` → `planning/story_plan.json.break_plan.occurrences`，每次精确绑定一个 SRC/ADD 与该单元内生成秒数；
- `approved_delivery_image` → 图任务用同一 SRC/ADD 返回的批准资产 ID，再写入对应 `source_units[].delivery_asset_ids` 或 `inserted_units[].delivery_asset_ids`。

归一化后必须分别校验两路 `completed_shot_ids / completed_source_shot_ids / completed_inserted_shot_ids` 与锁定 S/SRC/ADD 全集，并核对 `locked_semantic_hash / shot_map_sha256`、分镜图 SHA-256、源时间、生成时间、分镜描述、口播、六层、包装、吃食/掰开绑定、顺序和集合；不一致时只返工有冲突的 SRC/ADD。总控项目文件是后续编译、Word 导出和验收的唯一事实源，禁止图任务、文任务或第三个“对齐 Agent”再次拆分、重排或另建对齐表。

## 4. 总 TXT 与逐镜 TXT

```text
==================================================
S001｜“对应口播核心内容”
==================================================
原片时间：00:00.000–00:05.200
源分镜ID：SRC001、SRC002
独立生成时长：5.200秒
分镜描述：按原片 SRC001 的人物转头和 SRC002 的拿起产品连续动作，把新版第一句放在产品拿稳后；两段合并生成但均保留独立分镜图。
人物位置：男生始终在画面中；女生始终位于镜头后且不出镜
声音方式：女生镜外现场对白；男生屏内对白
产品形态：完整未破、手持展示
生成首帧：<路径、资产ID或待制作>
分镜图：SRC001=<批准图路径>；SRC002=<批准图路径>
核心主体：男生
核心动作：转头、拿起产品、看向镜头后人物
核心产品：完整未破目标产品
适用表演层：情绪触发、视线、五官、身体手部、呼吸停顿、声音口语

【口播稿】
女生：“第一句……”
男生：“第二句……”

【完整Prompt｜主体非空白字符数：XXXX】
从第一帧到最后一帧，画面禁止出现任何新增字幕、贴纸式文字、角标或水印；经用户确认的实体包装原有印刷不属于新增字幕。

视频核心：……
空间与人物锁：……
场景：……
表演与声音总原则：……

0.00–0.70秒：……
0.70–2.20秒：……
2.20–5.20秒：……

【原片动作对应】
- 原片 00:01.100–00:02.400 的咬食动作，对应生成镜内 0.70–2.20 秒：牙齿接触、咬入、产品离嘴、形成自然咬口。

【内容审核记录】
- 角色事实：男生在画面中；女生在镜头后，只通过现场声音和男生的视线反应存在。
- 台词事实：“……”由女生在镜外说；“……”由男生在画面内说。
- 动作事实：已写入原片的伸手、开盒、递出、咬食和收回动作及先后关系。
- 生成后像素复核：女生不得以脸、嘴、身体、影子、倒影或自拍形式出现；咬口必须由同一颗大福连续形成。
```

没有口播时：

```text
【口播稿】
无
```

规则：

- 每个镜头第一条动作时间从 `0.00秒` 开始；
- 每镜必须有 `源分镜ID / 分镜描述 / 分镜图 / 核心主体 / 核心动作 / 核心产品 / 适用表演层`。新增镜头的 `源分镜ID` 写 `新增镜头`，并在故事计划记录节奏和新版口播锚点。
- 合并镜头在 `分镜图` 中逐个列出每个源分镜对应的批准图；不能只放一张代表图。Word 同一 S 章节必须按源 ID 原顺序嵌入这些图片和说明。
- 每镜通常 2–3 句台词，动作逻辑优先；
- 较长单句原则上约 50 个汉字；
- 每镜在 `【表演与节奏统计】` 中记录口播有效字符数、实际可说时段、计划语速、句段数和单镜时长；有效字符只计汉字、字母和数字；
- 实际可说时段不得包含屏内人物入口、咬合、闭口咀嚼、原片确实存在的吞咽、必要换气、纯拟音和无声观察。吃食动作结束后可以马上接下一段屏内口播，不要求虚构吞咽或吃后反应；
- 单镜时长必须落在 `generation_time_policy` 的项目范围内。相邻短源分镜为满足至少4秒连续生成而合并时，在同一镜内保留全部 action beat、源 ID 和批准图；不得以 Prompt 3000–4000 字符要求为由合并不相关镜头；
- 只有用户/平台明确启用长度契约时，Prompt 主体才必须为3000–4000个非空白字符，并默认压在3000–3300安全区；未启用时使用最短可执行 Prompt。
- 只有用户/契约明确启用 40%–50% 表演占比时才把它作为阻断线；默认只记录表演字符数、Prompt 主体字符数和表演占比作诊断，内容审核仍只检查本镜可观察且适用的表演层。
- 字符数按去除全部空白后的 Prompt 主体计算，标题标注必须与程序实算一致；
- 不得以复制句子、同义反复、无关排除词或虚构不可观察细节凑长度；Prompt复制区只保留生成可执行内容，原片证据解释、审核过程、版本说明和统计方法全部放在复制区外；
- 可以删水词、同义限制和与本镜无关的通用知识，但 `核心主体 / 核心动作 / 核心产品` 的每一项必须能在 Prompt 主体中命中；有人物时还须覆盖 `适用表演层`。可检测的长句重复触发 `PROMPT_PADDING_DETECTED`；
- 产品形态只写中文，不写 `V1–V5`、`whole`、`bitten`、`person_eating` 等内部标签；
- 用户交付不使用 `PASS`、`FAIL`、`ERROR` 冒充内容审核结论；
- `【原片动作对应】` 要写动作事实和时间映射，不能只写“已覆盖”；
- `【内容审核记录】` 要写具体角色、台词、动作和待看像素，不能只写状态词。

DOCX 中每个 S 章节依次显示：原片绝对时间、源分镜 ID、按原片节奏与新版口播推算的分镜描述、该生成镜包含的全部经用户总览确认的分镜图、口播稿、独立生成时长、核心保留清单和可复制 Prompt。六层状态、置信度和缺口原因仅留内部 manifest，不作为 Word 栏目。所有文字必须是原生 Word 文字，不得转成文字卡图片；合并镜头必须为每个源分镜分别嵌图和标注，不能只放一张代表图。

### 分阶段 lint 命令

`--stage` 为 CLI 必填参数，防止文分支误做 Word、导出前误用旧 DOCX，或导出后根本没验 Word：

- `text_branch`：必须同时传 `--story-plan` 和 `--text-handoff`；禁止 `--delivery-dir`。
- `full_delivery_precompile`：必须传归一化 `--story-plan`；禁止 handoff 和 DOCX 目录。
- `full_delivery_postexport`：必须传 `--story-plan` 和 `--delivery-dir`；该阶段才检查最终 Word。

默认不强制 Prompt 字数。长度契约只能由 `story_plan.prompt_length_contract.enabled=true` 或 `--enforce-prompt-length` 显式启用；一旦启用，下限和上限必须同时为正数且同时阻断，禁止只开一边。

`full_delivery_postexport` 不再只检查 `.docx` 后缀。唯一终稿必须是真实 OPC/ZIP，包含 `[Content_Types].xml / word/document.xml / word/_rels/document.xml.rels`，能被 `python-docx` 打开，`document.xml` 实际引用至少一个图片 relationship，且原生可编辑正文能检出“准确秒数/原片时间、分镜描述、口播稿、即梦可复制 Prompt/完整 Prompt”。改后缀的假 DOCX、只放图、以及把文字烧进图片的文档都必须阻断。

## 5. 分镜确认卡

长 Prompt 前每镜提交短卡：

```text
S001｜标题
原片时间 / 生成时长：
源分镜ID / 合并原因：
分镜描述（原片节奏 + 新版口播落点）：
视频核心 / 本镜叙事作用：
角色与空间锁：
原片必须复刻的动作：
新版口播、说话人、声源与口型：
视线轨迹：
五官微反应：
身体、手部、呼吸与停顿：
讲话风格、语速、重音、尾音和口音来源（原声观察 / 已披露创作提案）：
台词同步视觉证据：
榴莲大福中文形态 / 状态链：
真实首帧 / 美观关键帧 / 待改生成首帧：
手口和道具占用：
核心主体 / 核心动作 / 核心产品 / 适用表演层：
仍需确认的冲突：
```

用户尚未发回新版口播时，可填写原片事实，但口播与台词视觉证据写 `等待用户发回新版口播`，不得自行补写。

## 6. 阶段状态与阻断码

`WAITING_FOR_REVISED_SCRIPT` 表示原口播已经交接、项目正常等待用户修订；它对应 `source_ready`/`awaiting_user_input`，不得写入错误型 `blocked_items`，也不得让文任务只报路径后结束。只有源视频不可读、锁冲突、结构/像素校验失败或事实无法满足时才使用真正阻断码。

- `MISSING_SOURCE_VIDEO`：没有原视频，无法复刻。
- `ORIGINAL_TRANSCRIPT_NOT_HANDED_OFF`：尚未提交原口播/原字幕。
- `WAITING_FOR_REVISED_SCRIPT`：非错误阶段状态；原口播已直接展示，源片先行分析完成，正常等待用户发回新版口播。
- `SPEAKER_IDENTITY_UNCONFIRMED`：A/B 与用户称呼未确认。
- `SPEAKER_IDENTITY_CONFLICT`：用户纠正、声画证据或既有角色锁互相冲突。
- `DIALOGUE_SPEAKER_MISMATCH`：口播稿与角色锁中的说话人不一致。
- `ROLE_ACTION_CONFLICT`：喂、吃、递、接等动作主语与角色锁不一致。
- `OFFSCREEN_CHARACTER_VISIBLE`：全程镜外人物被安排出镜。
- `PRONOUN_REFERENCE_CONFLICT`：代词指向导致人物关系或动作主语冲突。
- `SOURCE_ACTION_OMITTED`：原片可见主要动作未映射。
- `SCRIPT_OMITTED`：新版口播遗漏。
- `SCRIPT_REWRITTEN_WITHOUT_APPROVAL`：未经允许改写台词。
- `DELIVERY_MODE_CONFLICT`：屏内、镜外、电话或静默归属冲突。
- `CHEWING_SPEECH_CONFLICT`：咀嚼期间安排吃食者屏内说话。
- `EATING_PHASE_EVIDENCE_MISSING`：吃食事件没有记录原片可见/经用户批准的 `required_phases`，或 Prompt 遗漏其中阶段。
- `EATING_TEMPLATE_PHASE_FORCED`：原片没有吞咽或吃后反应，却把它们当成固定必选阶段或模板结尾。
- `EATING_PLAN_MISSING`：原视频达到30秒但缺全片吃食事件计划。
- `EATING_SHOT_QUOTA_MISSING`：原视频达到30秒但 `target` 小于 `max(source,3)`，或 occurrence 行数少于 target。
- `UNNECESSARY_EATING_SHOT_INSERTION`：新增数不等于 `max(0,3-source)`；原片已有三个或以上仍新增。
- `EATING_EVENTS_NOT_DISTRIBUTED`：相同或相邻 S 镜的一对吃食事件中至少含一个新增事件；两个相邻的真实 source 事件不以此码删除或改写。
- `EATING_RHYTHM_ANCHOR_MISSING`：吃食事件缺叙事段、原片/剪辑节奏锚点或新版口播锚点。
- `RESOURCE_OCCUPANCY_CONFLICT`：手、嘴、产品或道具互斥占用。
- `PRODUCT_STATE_INVALID`：状态跳步、互斥或物理不成立。
- `PRODUCT_COUNT_MISMATCH`：产品数量不守恒。
- `PLATE_LAYOUT_MISMATCH`：盘中产品虽然数量可能相同，但逐根中心位置、方向、上下层级、交叉点、露出端、遮挡比例或盘沿关系没有继承批准首帧，或被重排成整齐阵列。
- `PRODUCT_DIMENSION_MISMATCH`：黄油脆丝棒未保持用户确认的约 12 厘米长度，或同距离下没有保持约为 15 厘米盒宽 80% 的关系。
- `PACKAGE_DIMENSION_MISMATCH`：外盒未保持约 15×15×4.5 厘米的方形正面与浅厚度，或相对人物、产品、盘子的尺度异常。
- `FIRST_FRAME_CONFLICT`：生成首帧与 Prompt 起始状态冲突。
- `FACE_IDENTITY_NOT_TRANSFERRED`：目标成年人物身份不可辨认，结果仍接近原人物或只改变妆容。
- `HEAD_BODY_SCALE_MISMATCH`：头部相对肩宽、躯干、手掌或原构图明显过小/过大。
- `FACE_BODY_FUSION_ARTIFACT`：出现面具感、贴头感、发际线/耳朵/颈肩断裂或肤色纹理不连续。
- `CASCADING_IMAGE_EDIT`：在上一轮失败生成图上叠修，存在累积污染风险。
- `MOUTH_PRODUCT_CONTACT_BROKEN`：吃食镜头的嘴唇、牙齿、手指和产品接触链被破坏。
- `NON_TARGET_PERSON_CHANGED`：小孩或其他非目标人物的脸、发型、年龄、衣服或身份发生变化。
- `APPROVED_FRAME_NOT_REINSERTED`：修复图未按 S 编号回填逐镜目录，或总览/索引仍引用旧图。
- `PRODUCT_SPEC_STALE`：沿用过时或已否决产品规则。
- `PRODUCT_STATE_LABEL_NOT_CHINESE`：用户 TXT 使用内部英文或字母状态。
- `PROMPT_TOO_SHORT`：已启用长度契约时，完整编译主体少于指定下限。
- `PROMPT_TOO_LONG`：已明确启用长度契约时，完整编译主体超过当前项目上限（默认4000个非空白字符）。
- `PROMPT_COPY_REGION_CONTAMINATED`：即梦复制区混入原片证据解释、审核过程、版本说明、统计方法或与本镜无关的跨镜知识。
- `PROMPT_CHAR_COUNT_MISMATCH`：标题标注的主体字符数与程序实算不一致。
- `PROMPT_PADDING_DETECTED`：出现明显复制、同义堆叠或与本镜无关的长度填充。
- `DOC_PROMPT_SOURCE_DIVERGENCE`：Word 没有逐镜使用已校验 TXT 正文，或图片、台词、字符数与事实源不一致。
- `DOC_RENDER_NOT_VERIFIED`：DOCX 未渲染并逐页检查版式、中文字体、图片和截断。
- `GENERATION_TIME_NOT_ZERO`：镜内时间未从 0.00 秒开始。
- `NO_TEXT_RULE_MISSING`：缺无字幕无水印规则。
- `PERFORMANCE_DETAIL_MISSING`：缺视线、五官、身体/手部、呼吸/停顿或声音细节。
- `PERFORMANCE_SHARE_OUT_OF_RANGE`：只在占比契约已启用时，表演占比超出指定范围，或统计口径混入身份锁、产品/摄影约束和通用模板。
- `ACCENT_PLAN_MISSING`：有口播却没有具体可执行的口音与讲话方案。
- `ACCENT_PROPOSAL_NOT_DISCLOSED`：原声证据不足时提出了口音方案，却没有在对话中说明它属于创作提案。
- `GENERIC_SPEECH_PLACEHOLDER`：使用“沿用原片生活口语节奏”等泛化占位表达。
- `STRUCTURE_RESULT_MISREPRESENTED_AS_CONTENT_AUDIT`：把自动结构检查写成内容审核结论。
- `TXT_EXPORT_MISSING`：总 TXT、逐镜 TXT、角色锁或审核报告缺失。
- `PACING_FIELDS_MISSING`：故事计划或逐镜统计缺原视频总时长、生成镜最短/最长值、语速上限、实际可说时段或计划语速。
- `SHOT_DURATION_OUT_OF_POLICY`：单镜低于/超过项目声明范围，且不属于经记录的相邻短源分镜连续合并覆盖。
- `SCRIPT_SEGMENT_OVERLOAD`：单镜超过3个台词句段，或承载多个不能由同一动作闭环完成的叙事职责。
- `SPEECH_RATE_EXCEEDED`：屏内口播超过项目上限（默认5.0字/秒）或画外音超过项目上限（默认5.5字/秒）。
- `SPEECH_WINDOW_INVALID`：实际可说时段错误包含入口、咬合、闭口咀嚼、吞咽、必要换气、纯拟音或无声观察。
- `PROMPT_LENGTH_DRIVEN_SHOT_MERGE`：为了满足每镜Prompt字符下限、减少Prompt数量或沿用原片镜头数而合并本应拆开的台词与动作。
- `SOURCE_SHOT_INVENTORY_MISSING`：缺完整原分镜 inventory，或源分镜 ID 重复。
- `SOURCE_SHOT_EVIDENCE_MISSING`：源分镜缺绝对时间、动作、原始首帧或批准分镜图。
- `SOURCE_SHOT_COVERAGE_INCOMPLETE`：任一源分镜未映射到生成镜和 Word，或被重复吞并。
- `SHORT_SOURCE_SHOT_NOT_MERGED`：不足4秒的源分镜被删除、被单独生成不足4秒，或没有与相邻连续源分镜合并。
- `NONADJACENT_SOURCE_SHOT_MERGE`：合并了 inventory 中不相邻的源分镜、颠倒原顺序或跨越未纳入的源分镜。
- `SOURCE_TIMECODE_MAP_MISMATCH`：TXT 的原片时间/源分镜 ID 与故事计划映射不一致。
- `SHOT_DESCRIPTION_MISSING`：逐镜缺基于原片动作节奏和新版口播落点的分镜描述，或与故事计划不一致。
- `PROMPT_CORE_FACT_MISSING`：缺核心主体、核心动作、核心产品或适用表演层字段。
- `PROMPT_CORE_FACT_OMITTED`：锁定的核心主体、动作或产品没有出现在可复制 Prompt 中。
- `ASSET_REUSE_PLAN_MISSING`：换脸、换产品、换包装、换场景、补图、重拆镜或 Word 返工前没有建立资产复用计划。
- `HISTORICAL_ASSET_INVENTORY_MISSING`：没有检查用户点名的历史交付、旧 Word、人物/换脸库、产品/包装库或历史批准帧。
- `UNJUSTIFIED_REGENERATION`：存在可复用批准资产却重新生图，或补生没有合法原因和候选拒收记录。
- `AVATAR_PRODUCT_LAYER_CONFLICT`：同时换脸和换产品时没有分别绑定、授权和审核两个库层，或缺交叉像素保护区。
- `DELIVERY_FRAME_NOT_APPROVED`：参考图、source-first、美观/动作候选或未批准图片直接进入 Word。
- `USER_GALLERY_APPROVAL_MISSING`：完整逐图总览未展示、漏图、顺序/哈希不一致或用户未明确确认就进入 Word。
- `PACKAGE_SCENE_INTEGRATION_FAILED`：盒体正面/厚度比例、同规格尺寸、场景主光、接触影、边缘融合或平面贴图检查任一失败。
- `ACTIVE_ASSET_REVOCATION`：图片被撤销后未让 workflow、generation pack 和 export manifest 一同失效，仍试图沿用旧 Word 或旧用户回执。
- `DELIVERY_FRAME_ASPECT_MISMATCH`：交付分镜不是独立9:16，或确定性画幅整理裁掉关键人物、产品或动作。
- `DELIVERY_FRAME_COUNT_MISMATCH`：Word 实际内嵌图片数与资产复用计划的预期数量不一致。
- `DELIVERY_FRAME_DUPLICATED`：同内容图片无明确理由重复占位，或以拼图/总览冒充多张分镜。
- `SOURCE_PRODUCT_OR_TEXT_REGRESSION`：交付图退回原视频主体，或重新出现源字幕、水印、平台角标。
- `FINAL_DOCX_MISSING`：用户侧最终输出目录没有 DOCX。
- `FINAL_DOCX_COUNT_MISMATCH`：用户侧最终输出目录存在多个 DOCX，无法确定唯一终稿。
- `FINAL_DOCX_INVALID`：文件只是改后缀、OPC/ZIP 部件损坏、内容类型错误，或 `python-docx` 不能打开。
- `FINAL_DOCX_IMAGE_MISSING`：`document.xml` 没有使用任何真实图片 relationship。
- `FINAL_DOCX_EDITABLE_TEXT_MISSING`：最终 Word 缺准确秒数、分镜描述、口播稿或 Prompt 等原生可编辑正文，或疑似把文字做成图片。
- `LINT_STAGE_INPUT_MISSING / LINT_STAGE_INPUT_FORBIDDEN`：所声明的 lint 阶段缺必需事实文件，或提前读取了该阶段禁止的 handoff/DOCX。
- `PROMPT_LENGTH_CONTRACT_INVALID / PROMPT_LENGTH_CONTRACT_CONFLICT`：长度契约未显式启用却单独传 min/max，只启用单边界，或 CLI 与 story plan 矛盾。
- `TEXT_HANDOFF_*`：版本、`branch_role=text`、S/SRC/ADD 集合、时码、六层证据、吃食/掰开计划、完成状态或 `shot_map_sha256` 的结构化交接闸门失败。
- `USER_DELIVERY_ARTIFACT_LEAK`：用户侧最终输出目录混入 DOCX 之外的内部 TXT、JSON、Markdown、图片、manifest、审核表或对齐表。
