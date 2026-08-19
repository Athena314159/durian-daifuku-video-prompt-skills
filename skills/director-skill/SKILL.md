---
name: director-skill
description: >-
  Operate a persistent Jimeng short-video remix workflow: analyze source video and subtitles, lock story/roles/actions, inventory and reuse approved avatar/face/product/package/shot/Word assets, replace faces and products, approve first frames, compile prompts, align images with text, export TXT and DOCX from one canonical prompt source, and review results. Use for 即梦/Jimeng UGC、原视频复刻、逐镜首帧、换脸、换产品、历史资产复用、只补缺失镜头、Prompt 拆解、生成结果审核，以及“帮我对齐一下图文”“检查图文对齐”“同步图片和文字”“图片和 Prompt 对齐”“首帧和 Prompt 对齐”“Word 图文错位”“S编号和图片对不上”“重新导出对齐后的 Word”“执行 align-and-export”等指令。
---

# 即梦视频改款导演

把素材、字幕决策、分镜、首帧、知识库和 Prompt 保存为项目文件。不要依赖聊天记忆。

## 强制输入

开始分镜 Prompt 前必须取得：

1. 用户提供的原视频。
2. 用户提供的字幕稿。
3. 目标产品参考图或已批准的对应产品库条目。

缺少字幕稿时可以提取素材和分析原片，但不要决定人物讲话/画外音比例，也不要编译最终 Prompt。每个项目只选择一个产品规范；目标为黄油脆丝棒时，使用 `$extract-skill` 的 `references/products/butter-crisp-stick.md` 与实际资产，不得注入榴莲大福物理。

## 核心边界

1. 画面类型只允许 `product_showcase`、`person_product_showcase`、`person_eating`。
2. 声音方式按字幕段分别选择 `voiceover`、`on_screen_speech` 或 `silent`，不要预设全片画外音。
3. 人物吃产品时只能在咬前或咀嚼吞咽结束后讲话，禁止边咀嚼边说。
4. 先规划视频逻辑、产品展示/人物展示/人物吃产品占比和节奏，再写分镜 Prompt。
5. 每个分镜的 `source_first_frame` 必须是该分镜时间码的第一个真实帧。
6. `selected_beauty_keyframe` 是另选的美观参考帧，不能替代分镜首帧。
7. 只修改用户授权修改的元素；保持原构图时锁定人物、动作、机位、场景、光线和空间关系。
8. 生成前必须审核字幕策略、分镜结构、Prompt、首帧、产品参考绑定、节奏和商业权利。用户提供即梦结果或指出生成差异时，必须继续做结果审核与单镜返工；不得用“本 Skill 只负责生成前”跳过可见的产品、人物、动作或包装错误。
9. 任何重拆镜、换脸、换产品、换包装、换场景、补图或重做 Word 任务，必须先完成跨项目资产盘点与 `planning/asset_reuse_plan.json`；历史批准资产可满足新镜头时优先复用，只生成经逐镜记录后仍缺失或不合格的部分。

## 1. 初始化

```bash
python3 <skill-dir>/scripts/init_project.py \
  --name <project-name> \
  --output <projects-directory> \
  --product-profile <selected-product-profile> \
  --style-profile ugc-food-review-v1
```

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

重拆镜时允许一张历史批准帧映射到新的 S 编号，也允许多个历史动作帧分配给同一新镜；保留原文件，不在旧成品上叠修。人物和产品同时替换时，必须分别记录 avatar/face 资产与 product 资产，锁定各自允许编辑区域和交叉像素保护区，任何一层失败只重做受影响镜头。

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

转写或读取用户字幕稿后，先写 `planning/story_plan.json`：

1. 判断原视频以人物说话、画外音、混合还是无口播为主。
2. 拆分字幕的语义段，不按字数机械平均。
3. 对每段说明为何采用人物讲话、画外音或无口播。
4. 写清 `hook → product_promise → visual_proof → eating_experience → closing_payoff`。
5. 规划产品展示、人物展示产品、人物吃产品的时间占比和节奏。

不要把字幕稿直接平均铺到每个镜头。产品近景适合承担材质证据；人物展示适合建立信任；人物吃产品适合承担体验和情绪回报。

## 3. 拆分镜头并提取两类帧

先按动作和叙事完整性建立 `shots/shot_manifest.json`，再运行：

```bash
python3 <skill-dir>/scripts/extract_shot_frames.py \
  --project-dir <project-directory> \
  --candidates 5
```

脚本为每个分镜写入：

- `source_first_frame`：该分镜准确起始时间的首帧。
- `beauty_keyframe_candidates`：分镜内部候选美观帧。
- `selected_beauty_keyframe`：人工或视觉分析后单独选定。
- `approved_generation_first_frame`：从真实分镜首帧改图并批准的生视频首帧。

不得用美观帧冒充分镜首帧。美观帧只帮助理解人物、产品和风格。

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
- 每镜台词有效字符数、实际可说时段和计划语速是否成立；屏内人物入口、咬合、闭口咀嚼、吞咽、必要换气和纯拟音不得计入口型可说时段。
- 默认单镜不超过5秒、每镜不超过3个台词句段、屏内口播不超过5.0字/秒、画外音不超过5.5字/秒。超过时先拆镜或延长总时长，不得靠吞字、删词、口型滞后或字幕补词。
- Prompt 3000–4000字审核必须发生在正确拆镜之后；不得为了减少Prompt数量或满足单镜字符下限而合并镜头。

完整规则见 [references/prompt-rules.md](references/prompt-rules.md) 和 [references/workflow.md](references/workflow.md)。

## 5. 条件调用知识库

只调用 `library/knowledge_index.json` 中 `approved=true` 的条目。按 `visual_type`、`product_state`、`delivery_mode` 和 `narrative_role` 匹配 `applies_to`：

- `type=prompt` 或 `rule`：合并到对应镜头 Prompt。
- `type=image`：加入该镜头生成包的知识库参考图。

记录命中的条目 ID 和版本。不要把整个知识库无差别塞入每条 Prompt；冲突时以镜头硬约束、项目规则和产品规范优先。

黄油脆丝棒裸产品镜头必须实际绑定产品 `细节.jpg`，并把“实体片状覆盖层而非光滑基底上的图案”写入镜头硬约束。出现外盒时锁定用户确认的 `15 × 15 × 4.5 cm`，同时写明 1:1 正方形正面、约 0.3 边长盒厚和扁方盒；数字不能只存在于项目备注。

涉及产品或包装尺寸时，生成包必须先声明唯一 `scale_mode`。常规跨镜一致性使用 `physical_consistency`，执行单根 `12 × 2.5 × 1 cm`（正面目标4.8:1、成品4:1–5:1、侧面厚宽比约0.40）/ 15 cm盒面 / 0.80 与透视规则；只有用户明确要求“基于某张原始批准帧缩小或放大百分比”时使用 `relative_pixel_resize`。后者以该原帧为唯一视觉尺寸事实源，百分比一律按线性宽高倍率解释，并把绝对厘米值、0.80 投影目标和其他镜头尺寸排除出本轮画面硬约束；实体数字仅保留为元数据。两种模式同时出现时触发 `SCALE_MODE_COLLISION`，禁止生成。

## 6. 批准首帧与生成结果闸门

黄油脆丝棒零售盒必须逐盒执行装量和装载姿态审核：每只 `15 × 15 × 4.5 cm` 外盒总计只能有4包 `15 × 4.5 × 2 cm` 独立袋，盒内占位为每层2包、上下2层，四包长轴沿盒体15 cm纵深方向。`2×2` 不是开口处的展示阵列；尚未取出产品时，从15×4.5 cm窄侧开口主要看到4个自然错位、略斜搭靠并前后遮挡的锯齿热封袋端，袋身向盒内延伸。四条完整袋身同平面平码或把盒体画成敞口展示托盘触发 `BOX_POUCH_LAYOUT_MISMATCH`。遮挡不改变总数；从满盒取出1包后盒内只能剩3包并自然回落。第5包、十几包竖排、少于动作状态应有数量或动作前后自动补包均触发 `BOX_POUCH_COUNT_MISMATCH`，不得批准。

人物换脸必须实行参考层隔离。人物库只负责身份与表情，目标镜头的背景、家具、服装、身体姿态、手势、构图、机位与光线只能来自同编号原视频首帧。人物参考图内的墙面、沙发、窗、植物、旧产品、字幕、平台UI与暖光均是禁继承区域；使用前应优先调用由用户原图确定性裁切且带透明通道的身份图，禁止把生成式透明人物图当唯一身份源。背景泄漏触发 `AVATAR_BACKGROUND_LEAKAGE`，脸型或五官不匹配触发 `AVATAR_IDENTITY_MISMATCH`，两者都必须从同编号原始首帧重新做受限换脸。

ImageGen 刚返回的图一律视为“未审核候选”，不能因为已生成、整体顺眼或单项规则看似满足而称为批准帧、正确图或可交付图。先保存到候选/未批准目录，再查看全图，逐个裁切所有可见产品和包装，记录可测轮廓的长宽比、同规格一致性、相对手/盘/盒尺度和材质结论；全部通过后才复制到批准目录。向用户展示未完成 QA 的图时必须明确标注“候选、未批准”，不得让工具刚生成的预览承担交付含义。

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

`source_first_frame`（原镜头起始真实帧） → `face_reference`（若授权） + `product_references` → `approved_generation_first_frame`（仅基于原始真实帧的一次性编辑） → 视频 Prompt → 即梦生成结果。

把每个批准首帧写回 `asset_links.approved_generation_first_frame`；记录人物参考 ID、产品参考 ID、允许编辑元素、像素保护区和审核结论。不得在失败编辑图上继续叠修。涉及吃食时，脸部融合、嘴唇、牙齿、手指和产品接触链必须同时复核。

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

用户以后补充人物素材时写入 `library/avatar_library.json`。每个数字人至少保存：

- 授权状态和使用范围。
- 正脸、左右 45 度、侧脸、不同表情和光线参考。
- 年龄呈现、发型、肤色、妆容、体型及禁止改动项。

未明确选择数字人或肖像权未确认时，不自动换脸。

## 9. 编译与 Word 闭环交付

```bash
python3 <skill-dir>/scripts/pipeline.py lint --project-dir <project-directory>
python3 <skill-dir>/scripts/pipeline.py compile --project-dir <project-directory>
python3 <skill-dir>/scripts/export_jimeng_docx.py --project-dir <project-directory> \
  --out <project-directory>/exports/<项目名>_即梦逐镜视频Prompt.docx
python3 <skill-dir>/scripts/align_exports.py --project-dir <project-directory> \
  --docx <project-directory>/exports/<项目名>_即梦逐镜视频Prompt.docx --require-docx
```

修复所有 `ERROR` 后再编译。产出：

- `prompts/<shot-id>.md`：逐镜完整 Prompt。
- `prompts/generation_pack.json`：Prompt、三类帧、产品参考和知识库命中的映射。
- `review/shot_cards.md`：短分镜确认卡，包含画面/声音占比和节奏问题。
- `review/lint_report.json`：缺项、冲突和商业阻断。
- `exports/<项目名>_即梦逐镜视频Prompt.docx`：必须按 `references/word-delivery-contract.md` 输出；封面、镜头总览、每镜批准首帧、镜头信息、可复制 Prompt 和收尾审核表都来自同一批已校验的项目事实与逐镜 Prompt。

聊天中优先交付短分镜确认卡；除非用户要求，不展开全部长 Prompt。

Word 导出仅在 `pipeline.py lint` 没有 `ERROR` 时进行。导出后必须用 Documents Skill 渲染每一页并逐页看图；Word 的每镜 Prompt 必须逐字来自 `prompts/<shot-id>.md` 的代码块，禁止从摘要或聊天记录二次改写。详见 [references/word-delivery-contract.md](references/word-delivery-contract.md)。

把 `prompts/<shot-id>.md` 的 `text` 代码块设为唯一 Prompt 事实源。由 `align_exports.py` 派生总 TXT 和逐镜 TXT，并核对 DOCX 精确文本与实际内嵌图片哈希。任一镜头未对齐时写入 `review/alignment_manifest.json`、阻断交付并只修对应 S 编号；不得分别手改总 TXT、逐镜 TXT或 Word。

导出前还必须以 `--stage pre-word` 让 `audit_asset_reuse.py` 通过，并让 Word 实际内嵌图片数等于 `asset_reuse_plan.summary.expected_word_image_count`。Manifest 同时记录 `reused_frame_count`、`new_generation_count`、每张图片的来源资产 ID、旧/新 S 编号、哈希、尺寸、复用/补生决定与理由。

## 10. 工作台

UI 不替代 Skill，而是读写同一套 JSON 项目。架构和 API 边界见 [references/workbench-architecture.md](references/workbench-architecture.md)。

工作台至少提供：项目导入、字幕策略、故事结构、分镜时间线、首帧/美观帧选择、Prompt 检查、知识库、数字人库和生成包导出。所有关键决定必须可追溯到字幕段、原片时间码、知识库条目和规则版本。

## 10.5 增量纠错与 Skill 升级候选

发现新的跨项目失败模式时写入 `planning/skill_update_candidates.json`，然后运行：

```bash
python3 <skill-dir>/scripts/review_skill_candidates.py --project-dir <project-directory>
```

日常审核只读取 `status=new` 的结构化候选，不重读完整对话。脚本只生成去重和范围审核提案，不自动修改 Skill。只有 `status=approved` 且 `user_approved=true` 的跨项目规则，才允许使用 `$skill-creator` 写回对应 reference 或 script。项目专属人物、口播、镜头、临时比例和客户偏好不得提升为全局规则。

## 11. 商业闸门

商业发布前读取 [references/commercial-gate.md](references/commercial-gate.md)。未清原视频、肖像、音乐、字体、产品声明和参考素材权利时，可内部测试但不能标记为可发布。
