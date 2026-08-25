---
name: jimeng-video-remix-director
description: >-
  Build and operate a persistent commercial workflow for Jimeng short-video adaptation, including transcript-first source intake, automatic paired image/text task creation with exact sidebar naming, source-video and subtitle analysis, voice-over/on-screen speech/silence decisions, story logic and pacing, shot and action-beat splitting, exact temporal first frames kept separate from beauty keyframes, cross-project inventory and reuse of approved avatar/face, product/package, scene/shot, and prior Word assets before any regeneration, product replacement using the selected product-specific bible such as durian daifuku or 达尔顿黄油脆丝棒, prompt compilation, approved-first-frame validation, supplied-Jimeng-result review, failed product geometry or texture recovery, and avatar/product library management. Use for 图文双Agent、自动开两个对话、一个跑图一个跑文、先提取原片口播、即梦/Jimeng UGC、原视频复刻、字幕稿匹配、逐镜首帧、换脸、换产品、历史成品帧复用、只补缺失镜头、黄油脆丝棒、包装尺寸、产品材质走样、改图、Prompt 拆解、生成结果审核或本地视频工作台流程。
---

# 即梦视频改款导演

把素材、字幕决策、分镜、首帧、知识库和 Prompt 保存为项目文件。不要依赖聊天记忆。

## 图文双任务自动创建

用户明确要求“双 Agent、图文 Agent、开两个对话、一个跑图一个跑文”或同义流程时，必须完整读取并执行 [图文双任务自动创建与合并规则](references/dual-thread-launch.md)。当前对话作为唯一总控，先运行 `resolve_launch_contract.py` 选择阶段，再用 `list_threads` 查重、对缺失角色连续调用两次非阻塞 `create_thread`，必要时修正标题，并以双目标 `wait_threads(timeoutMs=0)` 验证真实启动。用户只说“图文 Agent 帮我跑一下这个视频”、尚无锁定新版口播或尚未明确完整 Word 交付时，先派 `execution_tier=source_intake`，不得伪装成 `full_delivery`。只有完整交付所需事实已锁定时才派 locked shot-map/hash。分支禁止递归开任务；总控必须亲自消费 handoff、在当前用户任务直接给出阶段成果，并独占 canonical 合并、DOCX、最终 QA 与最终交付。图线通过首张源帧核验后，必须立即在 commentary 用绝对本地路径直接嵌入可点击图片；约 25%/50%/75%/100% 节点展示新增图片或联系表，禁止只发进度文字或 handoff 路径。进入换品/生图后沿用同一进度合同：刚生成但未完成 QA 的图片必须标成“候选、未批准”，批准后按 `SRC`、再按 `ADD` 顺序逐张嵌入；总控收到任一 image-ready handoff 后在当前任务立即镜像同一图库，不等待文线。内部路径与 JSON 只用于审计，永远不能代替任务内图片本身。

## 执行档位（先选一个）

按用户当前要求选最小足够档位，不默认跑完所有交付链：

- `source_intake`：用户给了原视频但尚未提供/锁定新版口播，或只说“跑一下/先分析”而没有明确要求完整首帧、Prompt 与 Word 时启用。图线只做原片视觉清单与真实首帧，文线先提取原片可编辑口播；不换品、不生图、不编最终 Prompt、不导出 Word。图线与总控必须把已核验真实首帧直接嵌入各自用户可见任务，不能只报 handoff、JSON 或文件路径；文线口播一完成，总控同样立即贴出可复制修改的正文。两路阶段成果谁先完成就先展示，互不等待，也不等待产品、人物或商业授权输入。
- `diagnose_only`：只读取失败图、原帧、当镜 Prompt 和产品规范，定位根因与修正点。不转写、不全量抽帧、不生图、不编译长 Prompt、不导出 Word。
- `first_frame_only`：只做指定镜头的产品/包装绑定、改图与像素 QA。只建立当镜最小事实表；不生成 3000–4000 字视频 Prompt、Word、商业权利报告或全片语义审核。
- `prompt_only`：完成口播、角色、分镜和可复制 Prompt；不生图、不导出 Word。它只豁免批准生成首帧、候选成品图、资产复用图库回执和成品包装像素 QA，不豁免完整 SRC/ADD、角色锁、故事、情绪因果、结构化六层证据、无缝动作节拍、口播、商品/包装母版结构与 Prompt lint。必须把 `project.json.execution_tier` 和 workflow 同步设为 `prompt_only`，再由 `pipeline.py compile` 生成逐镜 canonical Prompt、汇总稿与交付回执；禁止聊天中手写一份“总控 Prompt”冒充编译结果。除非用户或平台明确要求，Prompt 不设 3000 字下限，优先保留可执行约束并去掉重复解释。
- `full_delivery`：用户明确要求完整首帧、逐镜 Prompt 与 Word，且新版口播已经锁定时启用全流程。若明确要求换品，还必须已绑定批准的目标产品参考；若不换品，`product_mode=preserve_source_product` 即可，不要求目标产品参考。TXT、JSON、manifest 和分支 handoff 是内部校验材料；换品候选与批准图仍须在任务内直接显示，但用户侧最终文件只交付 DOCX。只有用户/平台明确指定 3000–4000 字时才启用该字符契约。

当前档位不使用的产物不得预生成、预渲染或预审核。用户后续扩大交付范围时，再从已保存项目状态续跑。

## 强制输入

源片 intake 只强制要求用户提供原视频。开始最终分镜 Prompt 前必须取得：

1. 用户提供的原视频。
2. 用户确认或修改后的新版口播稿。
3. 仅在用户明确要求换品时，取得目标产品参考图或已批准的对应产品库条目。

用户未明确要求换品时，默认 `product_mode=preserve_source_product`：保留原片产品，不索要目标产品参考，不把项目或分支标记为 `blocked`。只有用户明确要求换品且缺目标产品参考时，登记非失败的 `pending_inputs=["target_product_reference"]`；已完成的视觉/口播 intake 仍保持对应 `*_ready` 状态。这不妨碍先完成原片口播和视觉 intake。缺少新版口播时可以提取素材和分析原片，但不要决定最终人物讲话/画外音比例，也不要编译最终 Prompt。每个换品项目只选择一个产品规范；目标为黄油脆丝棒时，使用 `$extract-video-prompt` 的 `references/products/butter-crisp-stick.md` 与实际资产，不得注入榴莲大福物理。阶段、产品模式、等待输入与 handoff 合同见 [references/source-intake-contract.md](references/source-intake-contract.md)。

## 核心边界

1. 画面类型只允许 `product_showcase`、`person_product_showcase`、`person_eating`。`product_showcase` 默认无手；黄油脆丝棒纯手部掰开镜用 `character.present=false`、`character.hands_only=true`，只出现动作所需双手与产品。
2. 声音方式按字幕段分别选择 `voiceover`、`on_screen_speech` 或 `silent`，不要预设全片画外音。
3. 人物吃产品时可在咬前，或咬合完成、产品离嘴后按原片节奏马上讲话。实际闭口咀嚼时不做完整台词口型；原片没有吞咽或吃后反应时不得强加。
4. 先规划视频逻辑、产品展示/人物展示/人物吃产品占比和节奏，再写分镜 Prompt。
5. 先建立完整 `SRC` 原片原子分镜清单。以 ffprobe duration/FPS 为权威，第一项从0开始、相邻边界无空洞/重叠、末项抵达视频末尾，并保存 `start_frame/end_frame`，容差不超过半个源帧。每个 `SRC` 必须恰好保留一次并有准确秒数、文字分镜描述、口播、真实首帧和至少一张自己的批准目标帧；同一 SRC 跨越多个动作关键状态时可按动作顺序保留多张，并逐张写职责。短于4秒时只与相邻 `SRC` 合并为不少于4秒的连续生成片段，不能删镜。按规则补入的吃食/掰开等新镜头使用独立 `ADD` 身份，写入 `inserted_units`，不得冒充 `SRC`；每个 `ADD` 同样必须有生成镜内秒数、可编辑描述、口播和至少一张自己的批准目标帧，并记录新增理由、节奏锚点、源片参考 ID 与参考帧。不同 unit 默认禁止复用同一 asset ID、路径或实际 SHA-256；相邻生成段的连续边界参考仍归真实 owner，不计作另一 unit 的最低覆盖。
6. `selected_beauty_keyframe` 是另选的美观参考帧，不能替代分镜首帧。
7. 只修改用户授权修改的元素；保持原构图时锁定人物、动作、机位、场景、光线和空间关系。
8. 生成前必须审核字幕策略、分镜结构、Prompt、首帧、产品参考绑定、节奏和商业权利。用户提供即梦结果或指出生成差异时，必须继续做结果审核与单镜返工；不得用“本 Skill 只负责生成前”跳过可见的产品、人物、动作或包装错误。
9. 当前项目内先做最小资产盘点。只有用户点名历史项目/旧 Word、要求复用，或当前项目缺失必要资产时，才扩展为跨项目盘点与完整 `planning/asset_reuse_plan.json`。历史批准资产可满足新镜头时优先复用。

## 1. 初始化

```bash
# 默认 source intake：保留原片产品，不绑定任何目标产品
python3 <skill-dir>/scripts/init_project.py \
  --name <project-name> \
  --output <projects-directory> \
  --style-profile ugc-food-review-v1

# 只有明确换品时追加
python3 <skill-dir>/scripts/init_project.py \
  --name <project-name> \
  --output <projects-directory> \
  --product-mode replace_product \
  --product-profile <selected-product-profile> \
  --style-profile ugc-food-review-v1
```

目标为榴莲大福时，新项目固定选择 `durian-daifuku-v2`，不得再用已被替换的 v1 半透明、颗粒流心或固定多拉带物理。v2 初始化必须自动写入按产品与状态匹配的知识条目、复制批准参考资产并保留每张图的角色边界。逐镜结构化尺度、像素预检、粉雾皮面、连续果泥、唯一终点和参考角色合同见 [榴莲大福 v2 总控集成合同](references/durian-daifuku-v2-integration.md)。旧项目只通过该合同规定的非破坏迁移副本升级，原项目与旧编译结果保持可回滚；活动副本不得保留内部旧版归档、非标准候选目录、旧 QA 回执、v1 参考目录或旧产品绑定，递归污染扫描未通过时迁移失败关闭。

任何 `durian-daifuku-v2` 生图或改图调用之前，必须先对该镜精确 `source_first_frame` 运行 `scripts/prepare_daifuku_pixel_preflight.py`：测量同景深锚点的像素宽度，计算目标大福宽高与 `bbox_xywh`，生成几何引导图，并把原帧和引导图 SHA-256 写回逐镜 `scale_lock.pixel_plan`。只有状态为 `authorized`、算术/边界框/原帧哈希/引导图哈希全部有效时才允许调用生图工具；“约 7 cm”“3.5–4 指宽”或知识库文字本身不构成生图授权。实际调用同时附精确原帧与几何引导图，并明确引导图只提供位置和尺寸，青色轮廓、十字、标签、文字绝不进入成品。缺失或失效时直接阻断，不先试生成一张再用 QA 发现过小。

任何生图或改图调用还必须先运行 `scripts/image_generation_gate.py authorize`。该命令只接受当前 release 的 `first_frame_only/full_delivery` 项目，绑定原始首帧、完整中文 Prompt、人物参考、产品参考、产品合同和适用几何导引图的路径与 SHA-256，并输出唯一授权回执；工具调用的图片输入必须逐项等于回执的 `required_image_inputs`。调用后用 `record-result` 绑定成图和联合 QA；无回执、哈希变化、旧 release、v1 产品、输入为候选图或半成品、联合 QA 未通过时均不可提升为批准帧。

同一像素计划最多发起一次初始生成。若结果仍越出宽度容差，不得用同一原帧、同一引导图和同一 Prompt 盲目重试；先判断模型是否违反几何计划，再只调整一个可审计变量或改用确定性局部缩放/合成。生成模型具有随机性，像素预检消除的是“未计算尺寸就盲生”的浪费，不等于承诺所有材质与手部遮挡一次必过。

主要事实文件：

- `planning/story_plan.json`：字幕、声音方式、叙事逻辑、画面占比和节奏。
- `planning/asset_reuse_plan.json`：人物/换脸、产品/包装、场景/构图、批准帧和旧 Word 画面的跨项目盘点、复用映射、补生理由与交付计数。
- `shots/shot_manifest.json`：逐镜事实、动作、声音、首帧和参考资产。
- `library/product_library.json`：可复用产品/包装资产索引、权利、版本、状态、参考图和批准成品；每个项目仍只从中选择一个目标产品。
- `library/product_bible.json`：当前唯一目标产品的固定结构、尺寸、状态和错误排除。
- `library/style_bible.json`：人物、场景、摄影和声音风格。
- `library/correction_memory.json`：长期纠错。
- `library/knowledge_index.json`：按镜头条件调用的 Prompt、规则和图片。
- `library/avatar_library.json`：以后补充的数字人及换脸授权资产。
- `planning/workflow_state.json`：项目独立的当前阶段、阻断项与下一步允许动作。
- `planning/skill_update_candidates.json`：项目中新发现的跨项目规则候选；只保存增量，不靠重读完整对话学习。
- `review/alignment_manifest.json`：按 S 编号核对 canonical Prompt、TXT、DOCX、批准首帧及人物/产品绑定。

字段见 [references/data-schema.md](references/data-schema.md)。

初始化后运行 `scripts/workflow_state.py --project-dir <project-directory> init`。恢复任务时先读取状态，只从当前阶段继续。多对话和低 Token 增量学习的边界见 [references/state-alignment-and-learning.md](references/state-alignment-and-learning.md)；需要图文对齐、断点恢复或规则候选审核时读取 [references/operator-prompts.md](references/operator-prompts.md)。

## 1.5 资产库层级与复用前置闸门

把资产按职责分层盘点，禁止把“参考资产”和“可直接交付的批准帧”混为一类：

1. **人物/换脸层**：`library/avatar_library.json`、获授权的 `face_reference`、已批准换脸帧。人物库只提供身份和授权事实；未明确选择或未清肖像权时不得绑定或复用。
2. **产品/包装层**：`library/product_bible.json`、`library/knowledge_index.json` 的批准图片条目、逐镜 `product_references`、已批准换产品/包装帧。产品库负责身份、结构、包装和状态，不自动继承参考图背景、人物或构图。
3. **场景/分镜层**：`source_first_frame`、美观/动作候选帧、`approved_generation_first_frame`、历史分镜成品和旧 Word 内嵌画面。只有批准帧可直接进入生成或 Word；候选帧须经用户授权与同等级 QA 后提升。
4. **交付层**：逐镜 Prompt、生成结果、Word、manifest 与审核报告。交付层必须引用前三层的稳定资产 ID、路径、哈希、旧/新 S 编号和审批状态。

开始任何生图前，搜索当前项目和用户点名的历史交付包，写入 `planning/asset_reuse_plan.json`。逐镜比较人物身份与授权、目标产品与状态、包装数量、场景与构图、动作语义、画幅、字幕水印、像素质量和连续性：匹配则 `reuse`；不匹配则 `reject` 并写可观察原因；只有 `missing`、文件损坏、权利不清、画幅无法安全整理或像素 QA 不通过时才允许 `new_generation`。不得因为重新拆镜、文件名变化、输出目录变化或希望“更好看”整批重生。

重拆镜时允许一张与当前 release、产品合同和联合结果回执一致的历史批准帧映射到新的 S 编号，也允许多个历史动作帧分配给同一新镜；保留原文件，不在旧成品上叠修。人物和产品同时替换时，必须分别记录 avatar/face 资产与 product 资产，并设置 `atomic_identity_product_required=true`、`retry_origin_policy=exact_original_source_only`、`partial_candidate_policy=diagnostic_only_never_reuse`；身份和产品必须在同一次请求、同一张候选图上同时通过，任一失败都整镜返回精确原始首帧重做。

在首次生图前和 Word 导出前分别运行：

```bash
python3 <skill-dir>/scripts/audit_asset_reuse.py \
  --plan <project-directory>/planning/asset_reuse_plan.json \
  --stage pre-generation
```

出现缺字段、无理由补生、未授权换脸、选中帧不可访问、交付帧非9:16、重复图片冒充多帧或计数不一致时，阻断后续操作。

旧项目缺少 `product_library.json` 或 `asset_reuse_plan.json` 时，先运行 `scripts/migrate_project_v1_1.py --project-dir <project-directory>` 做非破坏迁移并保留备份，再人工完成资产盘点；不得为通过 lint 伪造空的 reviewed 计划。

## 2. 分析原视频和字幕

```bash
python3 <skill-dir>/scripts/extract_video_assets.py \
  --video <source-video> \
  --project-dir <project-directory> \
  --interval 1.0 \
  --scene-threshold 0.28
```

`source_intake` 先提取原片可见字幕并运行自动语种检测，再用音频 ASR/口型作辅助。语言决策优先级是 `visible_subtitles → automatic_language_detection → speech_audio → lip_reading`；产品名、品牌名、包装上的国名/产地名永远不是 ASR 语种证据。例如画面与可见字幕为普通话时，产品名出现“印尼”不能把 ASR 改成印尼语。无法逐字确认的片段写 `[待核]` 和时间码，不猜词。文线完成后必须在 source-intake handoff 中提供 `transcript.editable_text`；总控立即在当前用户任务原样贴出该正文，请用户直接修改，不能只给路径、状态或“已提取”。

取得用户确认后的新版口播稿后，再写 `planning/story_plan.json`：

1. 判断原视频以人物说话、画外音、混合还是无口播为主。
2. 拆分字幕的语义段，不按字数机械平均。
3. 对每段说明为何采用人物讲话、画外音或无口播。
4. 写清 `hook → product_promise → visual_proof → eating_experience → closing_payoff`。
5. 规划产品展示、人物展示产品、人物吃产品的时间占比和节奏。
6. 把原视频从0秒到结尾拆成完整 `source_shots`；每项保存准确时间、源帧索引、原片分镜描述和原片首帧。总控以半帧容差验证首尾与全部相邻边界。
7. 原片总时长达到30秒时，先数已有吃食次数：已有3次不新增，不足时只补到3次。新增吃食点必须分散在非相邻节奏位并绑定新版口播锚点。
8. 黄油脆丝棒建立掰开计划：人物出镜掰开按原片/口播节奏决定；无人物出镜的纯手部产品掰开至少一镜，为硬性要求。

每个 SRC/ADD 同时写完整 `source_performance_layers`：情绪触发、视线、五官微反应、身体/手部准备、呼吸/停顿、声音/口语。SRC 每层只能用 `observed|audible|not_visible|not_applicable`；`template_supplement` 只能用于明确获授权的 ADD 创作增强，不得冒充原片识别。每层记录源时间、参考帧、可观察证据和置信度；不可见就明确不可见，不能用六句漂亮话补空。六层是内部证据，不是六个凑字栏目，也不得出现在用户 Word。

人物镜必须执行 [抖音带货人物情绪与原片节奏硬合同](references/commercial-emotion-rhythm-contract.md)，按 [商业情绪词库](references/commercial-emotion-lexicon.json) 准确命名原片已有的馋意、惊喜、回味、较真、亲近、信任或分享冲动，再翻译为时码内可见的眼神、五官、身体、手、呼吸和声音变化。必须分别写 `persona_drive / primary_emotion / secondary_emotions / undertone / residue / commercial_turn / evidence_basis`；只有“自然、克制、真实、平稳”直接失败。原片事实与 `creative_enhancement` 分轨，增强只有 `user_authorized` 才可进入 Prompt。

每个动作节拍从 0.00 秒连续覆盖到镜头结束，并写 ID、触发、情绪词、可见变化、声音变化、产品变化、镜头响应和下一动作。超过 2 秒的节拍继续拆，除非原片有可证明的持续保持并写 `hold_reason`。拿起、掰裂、两半分离、展示断面、送入口、咬下、离嘴、闭口咀嚼、恢复说话不得挤在一个长动作里；吃食和掰断 occurrence 必须回指相应 beat ID。可复制 Prompt 只保留正向可拍指令，不输出状态/置信度/缺口字段；限制词只进入末尾最小纠错附录，默认不超过主体15%。

新版口播全文在此阶段锁定 `text` 和程序实算的 `effective_characters`。后续所有 `source_units[].script_text + inserted_units[].script_text` 依生成时间拼接，以及所有有声 `shots[].audio.script_text` 依镜头顺序拼接，都必须与该全文逐字等价（只忽略标点与空白）；缺字、重复、用图片代替文字或估算字数均阻断完整交付。

不要把字幕稿直接平均铺到每个镜头。产品近景适合承担材质证据；人物展示适合建立信任；人物吃产品适合承担体验和情绪回报。

## 3. 拆分镜头并提取两类帧

先按动作和叙事完整性建立 `shots/shot_manifest.json`，再运行：

```bash
python3 <skill-dir>/scripts/extract_shot_frames.py \
  --project-dir <project-directory> \
  --candidates 5
```

脚本为每个分镜写入：

- `source_units[].source_first_frame`：每个 `SRC` 原片分镜准确起始时间的首帧。
- `inserted_units[].source_reference_frame`：每个 `ADD` 新增镜头绑定的真实源片表演/节奏参考；它不是该新增镜头的“原片首帧”。脚本不得用生成时间去源视频错误抽帧。
- `beauty_keyframe_candidates`：分镜内部候选美观帧。
- `selected_beauty_keyframe`：人工或视觉分析后单独选定。
- `approved_generation_first_frame`：从真实分镜首帧改图并批准的生视频首帧。

不得用美观帧冒充分镜首帧。美观帧只帮助理解人物、产品和风格。

小于4秒的原片分镜仍保留独立 `SRC` 身份、准确秒数、描述与自己的全部批准目标帧；只把时间相邻的短镜按原顺序合并到同一个不少于4秒的 `S` 连续生成片段。每个 `SRC` 在 Word 中仍有独立动作镜头卡与目标帧职责块，禁止用一张合并图替代多个源镜，也禁止只取多状态图中的第一张。`S` 可同时包含按生成时间排序的 `source_units` 与 `inserted_units`，也可为纯新增片段；两类 unit 必须共同从0.00连续覆盖整个 `S`，无空洞、无重叠。

### 3.5 黑框、模糊补边与全分镜覆盖闸门

原视频帧出现黑色画布、信箱边、黑色窄条，或用同帧模糊副本填充上下/左右空白时，这些区域只属于平台画布，不是场景事实。任何首帧编辑前，先运行：

```bash
python3 <skill-dir>/scripts/normalize_source_frame.py \
  --input <source-frame> --output <clean-9x16-frame> --report <crop-report.json>
```

- 自动裁切结果必须逐张查看；主体、脸、完整发型、手、产品、盘子、包装、字幕事实或关键场景线被切到时，使用 `--crop x,y,width,height` 写入人工审核后的唯一裁框重跑。
- 黑框不得传给 ImageGen，也不得保留为审美留白。交付候选四边出现连续黑带或黑角时标记 `BLACK_CANVAS_RETAINED` 并拒收。
- 模糊副本填充不得冒充真实景深。若清晰原生画面本身足以构成9:16，直接裁掉模糊带；若目标画幅确需新增区域，只允许重建为与相邻清晰场景连续的真实空间，并复核墙线、桌沿、窗框、人物轮廓和光线连续性。仍看见上下/左右镜像、拉伸或均匀高斯模糊带时标记 `BLURRED_FILL_RETAINED`。
- 在生成前建立全片时间轴总览、硬切/动作关键帧总览和准确首帧总览。总览必须覆盖从0秒到结尾，不得只挑产品特写。
- 建立 `source_shot_ids` 与 `candidate_shot_ids` 覆盖表；每个源镜头都必须出现在总览和候选清单中。人物镜头不得因换产品困难而省略。集合不相等时标记 `SHOT_COVERAGE_INCOMPLETE`，禁止称为整批完成。
- 清洗后的任意逐帧目录可用 `scripts/make_named_contact_sheet.py --input-dir <frames> --output <overview.jpg>` 生成带文件名标签的全量总览；不得用缩略图总览替代原尺寸单帧 QA。
- ImageGen 输出后再次检查完整画幅。生成模型重新补出黑框、黑色晕边、模糊副本、重复边缘或平台式背景填充时，从清洗后的原始帧单次重做，不在失败候选上继续叠修。

## 4. 分镜质量检查

每个镜头必须包含：

- `visual_type`、`narrative_role`、`script_segment_ids`。
- 选择该场景的理由。
- 可观察的场景、动作、产品状态、摄影和灯光。
- 人物镜头的身份、位置、视线、微表情和情绪变化。
- `audio.delivery_mode`、字幕原文、方式选择理由和讲话时间。
- 首帧、美观帧、产品参考图和可选数字人参考。

同时检查全片：

- 是否只有三类允许画面。
- 产品展示是否足够证明质感，而非只有人物空泛表达。
- 人物吃产品的占比是否足够形成体验回报，又没有拖慢节奏。
- 人物讲话和画外音占比是否符合原视频风格与字幕语气。
- 开头是否快速建立钩子，单镜头是否过长，动作是否完整。
- 每镜台词有效字符数、实际可说时段和计划语速是否成立；屏内人物入口、实际咬合、实际闭口咀嚼、原片确有的吞咽、必要换气和纯拟音不得计入口型可说时段。
- 连续生成片段默认4–8秒、每镜不超过3个台词句段、屏内口播不超过5.0字/秒、画外音不超过5.5字/秒。超过时按原片分镜和动作容量调整，不得删源镜、靠吞字、口型滞后或字幕补词。
- 若用户/平台已明确启用 Prompt 3000–4000字契约，长度审核必须发生在正确拆镜之后；未启用时不为字符下限扩写。

完整规则见 [references/prompt-rules.md](references/prompt-rules.md) 和 [references/workflow.md](references/workflow.md)。当前 Prompt 格式固定为 `narrative-six-layer-v1`；总控和文线不得在项目中另造第二套六层格式，也不得把跨镜通用六层原则复制到每镜尾部。

## 5. 条件调用知识库

只调用 `library/knowledge_index.json` 中 `approved=true` 的条目。按 `visual_type`、`product_state`、`delivery_mode` 和 `narrative_role` 匹配 `applies_to`：

- `type=prompt` 或 `rule`：合并到对应镜头 Prompt。
- `type=image`：加入该镜头生成包的知识库参考图。

记录命中的条目 ID 和版本。不要把整个知识库无差别塞入每条 Prompt；冲突时以镜头硬约束、项目规则和产品规范优先。

知识匹配必须包含 `product_profile`，不能让其他产品的同名状态串入。图片条目除路径外必须保存 `reference_role`、`allowed_inheritance`、`forbidden_inheritance` 和批准哈希；逐镜 `reference_roles` 与 `asset_links.product_references` 必须一一对应。榴莲大福 v2 缺任一对应关系时阻断编译，不得用“Prompt 已写不要继承”代替角色隔离。

榴莲大福每镜同时绑定 `integrity_lock` 与 `instance_lock`；多实例、摆盘、容器或包装镜再绑定 `arrangement_lock`。挖空大坑、顶部剥开、开盆状露馅或手撕洞不能冒充真实咬口；原片手持两颗/三颗必须等数替换，多颗共享约7厘米尺寸等级但保留独立手工形态，叠放只在接触处轻微压扁。盘/容器用稳定实例 ID 保持布局，拿起一颗后库存减一；包装和独立袋采用轻微错位、不同朝向与自然遮挡，禁止完美网格、等距、全平行。完整合同和拒收码见产品 Skill 的 `references/instance-topology-and-layout-continuity.md`。

榴莲大福包装镜还同时绑定 `shape_lock` 与 `package_content_lock`：容器/内托可以方，食品不可继承容器几何；盒内四颗使用稳定实例 ID，盒内、盘中、手持共用 `DF2-ROUND-7CM-001`。编译器必须写入“逐颗检查”，生成结果必须提交哈希绑定的 per-instance shape QA；任一实质可见实例出现直边、直角、切角、方格压模或跨场景形状漂移，触发 `DAIFUKU_PACKAGE_PRODUCT_GEOMETRY_INVALID`，不得批准。

黄油脆丝棒裸产品镜头必须实际绑定产品 `细节.jpg`，并把“实体片状覆盖层而非光滑基底上的图案”写入镜头硬约束。出现外盒时锁定用户确认的 `15 × 15 × 4.5 cm`，同时写明 1:1 正方形正面、约 0.3 边长盒厚和扁方盒；数字不能只存在于项目备注。

出现可读外盒印刷时，正面、侧面、顶面分别绑定批准母版，记录实际可见区域与自然遮挡/出框区域。文字和图案用单应性或受保护的确定性合成投射，生成模型只负责盒体几何、折边、光照、反光、接触影和边缘融合。允许正常出框；禁止为露全图案缩小、移动、重排或压扁母版。

图片模型完成盒体几何后、最终批准前，使用本 Skill 的确定性母版投射工具；`--quad` 固定按左上、右上、右下、左下，手部遮挡或自然出框可用与候选图同尺寸的灰度 mask：

```bash
python3 <skill-dir>/scripts/project_package_master.py \
  --candidate <box-geometry-candidate.png> \
  --master <approved-face-master.png> \
  --face <front|side|top> \
  --quad <x1,y1,x2,y2,x3,y3,x4,y4> \
  --visible-mask <optional-visible-mask.png> \
  --output <projected-candidate.png> \
  --manifest <project-directory>/review/<unit>-<face>.projection.json
```

投影 manifest 留在内部包装 QA 证据中，必须复核 candidate/master/output 的 SHA-256 与尺寸、目标四边形、mask、`projection_method=homography` 和 `model_redraw_used=false`；它不能替代最终可见文字、方向、跨棱和遮挡检查，也不写入用户 Word。

`qa_status=approved` 不能靠 Agent 自报。每个可见盒面必须保存原尺寸候选裁切、候选与母版真实 SHA-256、全部应见文字/Logo/色块/图案检查点，以及文字可读性、方向/镜像、跨棱登记、遮挡范围、模型重绘和意外缺块结论；检查点有一项未 `matched`、文件/哈希不一致或只有口头“已检查”时，记录对应包装错误并阻断整张图进入 Word。

涉及产品或包装尺寸时，生成包必须先声明唯一 `scale_mode`。常规跨镜一致性使用 `physical_consistency`，执行单根 `12 × 2.5 × 1 cm`（正面目标4.8:1、成品4:1–5:1、侧面厚宽比约0.40）/ 15 cm盒面 / 0.80 与透视规则；只有用户明确要求“基于某张原始批准帧缩小或放大百分比”时使用 `relative_pixel_resize`。后者以该原帧为唯一视觉尺寸事实源，百分比一律按线性宽高倍率解释，并把绝对厘米值、0.80 投影目标和其他镜头尺寸排除出本轮画面硬约束；实体数字仅保留为元数据。两种模式同时出现时触发 `SCALE_MODE_COLLISION`，禁止生成。

原视频的旧产品/旧包装默认只提供动作、位置和遮挡语义，不提供目标尺度。为每个换主体镜头记录 `source_scale_role=compatible_scale_anchor|pose_only_incompatible_scale`；未声明时按 `pose_only_incompatible_scale` 处理。目标棒与目标盒未在同镜、未处于近似同深度或朝向不可比时，禁止把 `12:15=0.80` 写入该镜 Prompt。纸箱/盘子/桌面可见容积无法容纳目标规格与数量时，先触发 `SOURCE_CONTAINER_CAPACITY_CONFLICT`，不得缩小或放大目标包装强行保留原堆放密度。完整规则以 `$extract-video-prompt/references/products/butter-crisp-stick.md` 为唯一事实源。

## 6. 批准首帧与生成结果闸门

先做重力与接触审核。手持产品必须能看到与棒体轮廓相符的指腹接触、局部遮挡、受力指节或包装褶皱，并有一致的近距离接触阴影；只在产品两侧摆出手指、没有接触遮挡，或产品与手掌之间留有可见空气缝时，标记 `OBJECT_FLOATING_OR_CONTACT_INVALID`。盘装、桌面、盒内产品必须由盘面、桌面、盒底或其他产品真实承重，并出现与场景主光一致的接触阴影；不得悬在空中、穿过手指/盘沿或无重力搭接。接触失败整张拒收，不能用“画面整体自然”覆盖。

黄油脆丝棒零售盒必须逐盒执行装量和装载姿态审核：每只 `15 × 15 × 4.5 cm` 外盒总计必须且只能有6包 `15 × 4.5 × 2 cm` 独立袋，盒内占位为每层3包、上下2层，六包长轴沿盒体15 cm纵深方向；同层三包并排宽度约13.5 cm，上下两层合计厚度约4 cm。`3×2` 只描述不可见的盒内占位，不是开口处的展示阵列；尚未取出产品时，从15×4.5 cm窄侧开口主要看到6个自然错位、略斜搭靠并前后遮挡的锯齿热封袋端，袋身向盒内延伸。六条完整袋身同平面平码或把盒体画成敞口展示托盘触发 `BOX_POUCH_LAYOUT_MISMATCH`。遮挡可以减少可见端部，但不改变盒内6包总数；从满盒取出1包后盒内必须且只能剩5包并自然回落。第7包、十几包竖排、少于动作状态应有数量或动作前后自动补包均触发 `BOX_POUCH_COUNT_MISMATCH`，不得批准。

人物换脸必须实行参考层隔离。人物库只负责身份与表情，目标镜头的背景、家具、服装、身体姿态、手势、构图、机位与光线只能来自同编号原视频首帧。人物参考图内的墙面、沙发、窗、植物、旧产品、字幕、平台UI与暖光均是禁继承区域；使用前应优先调用由用户原图确定性裁切且带透明通道的身份图，禁止把生成式透明人物图当唯一身份源。背景泄漏触发 `AVATAR_BACKGROUND_LEAKAGE`，脸型或五官不匹配触发 `AVATAR_IDENTITY_MISMATCH`，两者都必须从同编号原始首帧重新做受限换脸。

ImageGen 刚返回的图一律视为“未审核候选”，不能因为已生成、整体顺眼或单项规则看似满足而称为批准帧、正确图或可交付图。先保存到候选/未批准目录，再查看全图，逐个裁切所有可见产品和包装，记录可测轮廓的长宽比、同规格一致性、相对手/盘/盒尺度和材质结论。可见外盒逐盒记录正面宽高比 `0.95–1.05`、厚度/正面 `0.25–0.35`、同框同规格结论，并同时证明场景主光方向匹配、接触阴影存在、边缘融合通过、`flat_cutout=false`；任一缺失不得提升。

视觉 QA 通过只得到“视觉已审、待用户确认”，仍不能进入 Word。总控必须先在当前用户任务按锁定 `SRC/ADD → asset` 顺序嵌入全部单图并给出总览，记录绑定全部图片 SHA-256 的 `gallery_receipt`；只有用户明确确认后，图片才能标成 `approval_status=user_approved`，且每张 `user_approval` 必须绑定同一 `display_receipt_id` 和图片哈希。总览漏图、顺序不一致、图片更新后沿用旧回执或只给联系表路径均阻断 Word。

盘装裸棒不能只测一根代表全盘。对每根完整可辨认棒体分别记录长宽比；目标为 `12 × 2.5 × 1 cm` 时，批准安全区使用 `4.6:1–4.9:1`，且同盘完整棒体的长度与宽度差异须能由透视解释。出现同盘有的短粗、有的超长，或任何完整棒体低于4.6:1/高于4.9:1，触发 `MULTI_INSTANCE_DIMENSION_INCONSISTENT`，整张不得交付。

批准前必须先做全画面产品与容器清点，逐项记录运输箱、零售盒、独立袋、透明内托、盘子和裸产品的数量与包含关系；再逐只裁切所有可见盒子检查 `15 × 15 × 4.5 cm` 三轴，而不是以一只盒子的方形正面代表整张通过。原帧运输箱内有 N 盒时，结果必须仍在同一运输箱内恰好 N 盒，禁止减少盒数、移出箱外、散入独立袋或新增展示托盘。盘子只装裸产品；盘内出现包装、或盘外凭空出现规则托盘，触发 `PACKAGE_TOPOLOGY_MISMATCH` 或 `PLATE_CONTAINER_CONTAMINATION`，不得交付。

逐镜容器清点同时是包装存在性白名单。原片该镜没有零售外盒时，不得因为产品知识库、跨镜模板或相邻镜头出现外盒而新增外盒；原片只有裸产品时只允许替换裸产品。未经原片对象清单授权而新增外盒、独立袋或透明托盒，触发 `UNAUTHORIZED_PACKAGE_INJECTION`。黄油脆丝棒独立袋统一为 `15 × 4.5 × 2 cm`，袋内固定包含透明浅边单根托盒，物理分层为 `金色膜袋 → 透明托盒 → 12 × 2.5 × 1 cm单根产品`；开袋后托盒必须连同产品沿长轴滑出，不能让产品直接贴膜裸装或凭空消失托盒。

批准首帧前，查看全图和每个产品/包装原尺寸局部。黄油脆丝棒局部必须与 `细节.jpg` 同尺度并排检查：片状碎片顶面、侧边厚度、前后遮挡、翘边、窄缝、微投影和轮廓凸出缺一不可。贴图、印刷纹、划痕、浅浮雕、压花、均匀卷曲细线或光滑橙色基底触发 `PRODUCT_MICROSTRUCTURE_FLATTENED`；不能以“微纹理差异”批准。

盘装镜头必须把每根产品当作独立实例，以批准首帧记录中心位置、长轴方向、上下层级、交叉点、露出端、遮挡比例和盘沿关系。产品参考图只负责形体与材质，不得把参考图的堆法覆盖到已有镜头。原帧为松散交错堆放而结果变成两排平码、平行阵列、扇形、网格、金字塔或广告式整齐陈列时，触发 `PLATE_LAYOUT_MISMATCH`；即使根数相同也不得批准。

外盒在两种模式下都逐盒检查 1:1 正面、约为边长 30% 的盒厚和同框多盒结构一致。`physical_consistency` 额外检查 `15 × 15 × 4.5 cm` 的可见比例；`relative_pixel_resize` 改为检查相对唯一源帧的目标线性倍率及人物、盘子、独立袋、镜头等 `1.00` 不变量。薄纸片、立方体、砖形厚盒、长条盒、不同盒厚或明显过缩/欠缩均不得批准。

同时执行产品—包装尺度联合审核。`physical_consistency` 的产品三轴基准为 `12 × 2.5 × 1 cm`，正面目标长宽比4.8:1、成品4:1–5:1、侧面厚宽比约0.40；产品—盒面物理基准为 `12 cm 单根 / 15 cm 盒面边长 = 0.80`，只有同平面且朝向可比时才直接测像素比，跨景深先校正。`relative_pixel_resize` 则记录源/结果边界框和每类线性倍率；只有确定性蒙版变换并测量后才能声称精确百分比，生成式编辑只能标记近似。包装尺寸、单根长度、实体片状微结构、盘中拓扑和 `1.00` 不变量必须同轮通过；修一项导致另一项回退时触发 `PRODUCT_PACKAGE_JOINT_LOCK_FAILED`，从原始批准帧单次重做，禁止继续叠修。

收到即梦视频后至少检查 0 秒、产品开始移动后、接近嘴部前和接触/断裂时。产品实体碎片的遮挡、高光和微阴影须随运动产生连续轻微视差；运动中融平成纹样仍判失败。从原场景首帧和正确产品参考单次重做失败镜，不在失败视频或失败生成图上叠修。

## 7. 换脸、换产品与首帧回填

只在用户提供目标人物参考、授权范围与明确镜头范围后启动换脸；未绑定数字人时，不得自动改脸。每个需要改脸/换产品的镜头必须形成一条可追溯链：

`source_first_frame`（不可变原镜头起始真实帧） → 同一授权请求内的 `face_reference`（若授权） + `product_references` + 适用 `scale_guide` → 联合 QA 通过的 `approved_generation_first_frame` → 视频 Prompt → 即梦生成结果。

把每个批准首帧写回 `asset_links.approved_generation_first_frame`；同时写入匹配该图片 SHA-256 的 `image_generation_result_receipt`，记录人物参考 ID、产品参考 ID、允许编辑元素、像素保护区和联合审核结论。失败图只进诊断记录，禁止成为重试输入、成功字段保护帧、历史批准帧或下一镜母资产。涉及吃食时，脸部融合、嘴唇、牙齿、手指和产品接触链必须同时复核。

## 8. 数字人库

### 内置人物：男性1（AV-002）

- 将 `assets/avatars/male-1/male1_turnaround.png` 作为男性1的正面、侧面与背面身份参考，身份名固定写作“男性1”，资产 ID 固定为 `AV-002`。
- 男性1参考图只负责成年东亚男性的脸型、五官、发际线与头部结构；不得继承参考图的黑色无袖服装、黑裤、鞋、白底、站姿或纯黑发色。
- 当用户要求“换成男性1但头发仍跟原视频一样”时，身份与头发分层：脸部身份取 `AV-002`，发型轮廓、长度、颜色、挑染、发根与高光一律继承同编号原视频首帧。当前黄油脆丝棒红围裙项目锁定为短刺发、深色发根、棕金/浅金发梢；变成纯黑发触发 `AVATAR_HAIR_OVERRIDE_MISMATCH`。
- 用户明确指定男性1并授权镜头范围后，所有出现目标男性的镜头连续执行换头；同框其他人物、眼镜、服装、身体、手势、产品、包装、盘子、场景、机位与光线均为像素保护区。不能只换部分镜头造成身份跳变。

### 换脸前的场景来源锁

- 人物库图只承担身份参考。优先使用透明背景的头部与完整发型抠图，只控制脸部身份和发型，不控制环境。
- 每镜原视频首帧是背景、家具、服装、首饰、姿势、双手、机位、光线、景深和遮挡关系的唯一场景参考。
- 生成前抠除或遮罩人物参考背景。若人物库中的沙发、墙面、窗户、植物、服装、首饰、姿势或光线渗入镜头，标记 `AVATAR_BACKGROUND_LEAKAGE`，拒收，并从该镜原片首帧加透明身份抠图重新生成。

### 多镜盘面与扩画硬门

- 黄油脆丝棒等多实例盘装镜头在任何整批生图前，必须先读取产品知识库中的“多镜盘装母资产与库存连续性”，建立逐镜库存账本并生成每个库存阶段的唯一盘面母资产。母资产必须是已经融入目标场景的完整盘面成功字段；严禁把白底盘、椭圆/圆形蒙版、硬边盘片或带摄影棚背景的产品层贴入人物镜头。出现白边、双盘沿、产品被裁断、盘下额外一排或光影透视不一致时，整张退回原始真实帧重做。
- 需要同时换头与换产品时，禁止“身份清洁帧→产品编辑”或“产品成功帧→再修身份”的分步链。必须从本镜同一精确原始真实帧，在一次原子请求中同时提交身份参考、产品参考、适用尺寸导引图和完整 Prompt；身份、头颈、产品、尺寸、材质、接触、字幕水印与场景锁在同一候选上联合通过。任一项失败，整张只作诊断并返回原图重生，禁止冻结或复用任何局部成功字段。
- 精确计数、同规格尺寸、实例 ID、剩余棒位和拿取事件属于首帧批准硬门；任一失败时只返工对应层，不得把错误候选作为下一镜盘面参考。
- 原片3:4而交付9:16时，先在原生3:4完成换头、换产品和手部适配，再以确定性上下补画输出9:16。主画面保持等宽、等比例、无裁切、无拉伸；禁止模型扩画导致门框、墙线、窗框、桌沿、托盘边缘或人物边缘畸变。
- 上述两项未形成机器可读锁定表时，整批首帧生成不得启动。建议文件名为 `planning/product_continuity_lock.json`，至少包含 `plate_master`、`continuity`、`canvas_lock`、`avatar_lock` 和硬拒收码。

用户以后补充人物素材时写入 `library/avatar_library.json`。每个数字人至少保存：

- 授权状态和使用范围。
- 正脸、左右 45 度、侧脸、不同表情和光线参考。
- 年龄呈现、发型、肤色、妆容、体型及禁止改动项。

未明确选择数字人或肖像权未确认时，不自动换脸。

## 9. 编译与 Word 闭环交付

```bash
python3 <skill-dir>/scripts/pipeline.py lint --project-dir <project-directory>
python3 <skill-dir>/scripts/pipeline.py compile --project-dir <project-directory>
python3 <skill-dir>/scripts/pipeline.py verify-prompt-delivery --project-dir <project-directory>
python3 <skill-dir>/scripts/export_jimeng_docx.py --project-dir <project-directory> \
  --out <user-output-directory>/<项目名>_即梦逐分镜执行稿.docx \
  --manifest-out <project-directory>/review/<项目名>_即梦逐分镜执行稿.manifest.json
python3 <skill-dir>/scripts/align_exports.py --project-dir <project-directory> \
  --docx <user-output-directory>/<项目名>_即梦逐分镜执行稿.docx --require-docx
```

修复所有 `ERROR` 后再编译。产出：

- `prompts/<shot-id>.md`：逐镜完整 Prompt。
- `prompts/generation_pack.json`：带 `compile_id` 的不可混用编译合同；逐 S 保存完整 `source_units`、`inserted_units`、Prompt/文件哈希和字符数，顶层保存 canonical input hashes 与项目唯一的 `prompt_length_contract`。Word 导出必须与 `prompts/history/<compile_id>/input_snapshot.json` 完全同批。
- `review/prompt_delivery_receipt.json`：编译器签发的 Prompt 交付授权；只有 `verify-prompt-delivery` 返回 `authorized` 才能向用户交付。`prompt_only` 另外生成 `prompts/canonical_prompt_only.md`，它只能逐字汇总同批 `Sxxx.md`，不能手写、改写或作为第二事实源。
- `review/shot_cards.md`：短分镜确认卡，包含画面/声音占比和节奏问题。
- `review/lint_report.json`：缺项、冲突和商业阻断。
- `<user-output-directory>/<项目名>_即梦逐分镜执行稿.docx`：必须按 `references/word-delivery-contract.md` 输出，固定包含封面、`当前结构结论`、`生成段总览`、逐 S 的 `动作镜头对应`、`目标帧与职责`、`可复制Prompt原文` 和最终检查清单。每个 `SRC` 保留准确原片秒数、生成镜内秒数、文字分镜描述、口播及至少一张经用户确认的目标帧；每个 `ADD` 标明“无原片秒数”，并保留生成镜内秒数、新增理由、节奏锚点、源片依据、口播及至少一张经用户确认的目标帧。六层证据只留在内部 manifest，不作为 Word 栏目。同一 unit 的多状态图必须全部按动作顺序嵌入并逐张标明 unit ID、asset ID 与职责；掰开与包装镜显示对应证据块；每个 `S` 末尾放 canonical 可复制 Prompt。用户输出目录只允许这一份 DOCX。

`review/shot_cards.md`、TXT、JSON、manifest、对齐报告和分支 handoff 只供内部校验，不作为用户交付，也不在最终回复中列出。

Word 导出仅在 `pipeline.py lint` 没有 `ERROR` 时进行。导出后必须用 Documents Skill 渲染每一页并逐页看图；Word 的每镜 Prompt 必须逐字来自 `prompts/<shot-id>.md` 的代码块，禁止从摘要或聊天记录二次改写。详见 [references/word-delivery-contract.md](references/word-delivery-contract.md)。

把 `prompts/<shot-id>.md` 的 `text` 代码块设为唯一 Prompt 事实源，Prompt 长度只读取 `project.json.prompt_length_contract`。`prompts/` 根目录除当前 `Sxxx.md` 与编译器生成的 `canonical_prompt_only.md` 外出现任何 Markdown，立即触发 `NON_CANONICAL_PROMPT_BYPASS`；修改 canonical 输入、逐镜 Prompt、generation pack、汇总稿、回执或 workflow 任一哈希后，交付授权立即失效并要求重编译。`align_exports.py` 是只读最终审计器：它不得回写 generation pack、Prompt、图片、TXT 或 Word，只写内部 alignment manifest/workflow 状态；P0 校验包括当前 canonical 与 `compile_id`/history input snapshot/export manifest 的新鲜度、每个 unit 动作卡的准确秒数/描述/口播、内部六层 manifest 完整且未泄漏为 Word 栏目，以及 `目标帧与职责` 中每张图片的 OOXML relationship、实际 media bytes、顺序、owner unit、asset ID、职责和 SHA-256。缺一张、多图颠倒、caption owner 错写、图片字节互换、跨 unit 冒用或 Prompt 合同出现第二事实源均阻断。

任何后续审核撤销图片时，立即运行 `scripts/invalidate_revoked_delivery.py --project-dir <project> --revocation <active-revocation.json>`。脚本把 workflow、generation pack 和 export manifest 标为 stale，并关闭 Word 导出授权。只有重新生成受影响资产、重新做完整总览、取得新的哈希绑定用户回执、重新编译与导出后才能恢复；旧 Word、旧回执和未受影响镜头的旧 compile 均不得假装仍然有效。

导出前还必须以 `--stage pre-word` 让 `audit_asset_reuse.py` 通过，并让 Word 的 owner 图片出现数等于全部 unit 的有序 `delivery_asset_ids` 总长度；显式连续边界参考另行计数，不能冒充另一 unit 的覆盖。Manifest 留在项目 `review/` 内部，同时记录 `reused_frame_count`、`new_generation_count`、每张图片的真实 owner、来源资产 ID、旧/新 `SRC/ADD/S` 编号、哈希、职责、尺寸、复用/补生决定与理由。最终确认用户输出目录除唯一 DOCX 外没有 TXT、JSON、Markdown、manifest、图片目录或对齐表。

## 10. 工作台

UI 不替代 Skill，而是读写同一套 JSON 项目。架构和 API 边界见 [references/workbench-architecture.md](references/workbench-architecture.md)。

工作台至少提供：项目导入、字幕策略、故事结构、分镜时间线、首帧/美观帧选择、Prompt 检查、知识库、数字人库和生成包导出。所有关键决定必须可追溯到字幕段、原片时间码、知识库条目和规则版本。

## 10.5 增量纠错与 Skill 升级候选

发现新的跨项目失败模式时写入 `planning/skill_update_candidates.json`，然后运行：

```bash
python3 <skill-dir>/scripts/review_skill_candidates.py --project-dir <project-directory>
```

日常审核只读取 `status=new` 的结构化候选，不重读完整对话。脚本只生成去重和范围审核提案，不自动修改 Skill。只有 `status=approved` 且 `user_approved=true` 的跨项目规则，才允许使用 `$skill-creator` 写回对应 reference 或 script。项目专属人物、口播、镜头、临时比例和客户偏好不得提升为全局规则。

任何写回都必须完整读取并执行 [Skill 变更与发布门禁](references/skill-change-governance.md)。禁止直接编辑 live Skill；从当前稳定快照建立候选版本，更新成对 release manifest，运行 `scripts/skill_release_gate.py` 的旧黄金案例、新案例和全量发布测试，通过后才允许保存快照并安装。项目用 `project.json.skill_release_lock` 固定 `bundle_release_id` 与 `prompt_authoring_contract`，`auto_upgrade=false`；live 更新不得静默改变旧项目。

## 11. 商业闸门

商业发布前读取 [references/commercial-gate.md](references/commercial-gate.md)。未清原视频、肖像、音乐、字体、产品声明和参考素材权利时，可内部测试但不能标记为可发布。
