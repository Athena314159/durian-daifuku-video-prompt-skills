---
name: jimeng-video-remix-director
description: "Build and operate a persistent commercial workflow for Jimeng short-video adaptation: analyze a source video and user subtitle script, decide per segment between voice-over/on-screen speech/silence, reason about story logic and visual pacing, restrict shots to product showcase/person-product showcase/person eating, split shots and action beats, extract each shot's exact first temporal frame separately from beauty keyframes, preserve source composition while replacing the product with durian daifuku, compile detailed prompts, enforce product/style/correction/knowledge memory, manage future face-replacement avatar assets, and create compact pre-generation shot cards. Use for 即梦/Jimeng 榴莲大福 UGC、原视频复刻、字幕稿匹配、人物说话与画外音规划、逐镜首帧、改图、Prompt 拆解、知识库引用或本地视频工作台流程。"
---

# 即梦视频改款导演

把素材、字幕决策、分镜、首帧、知识库和 Prompt 保存为项目文件。不要依赖聊天记忆。

## 强制输入

开始分镜 Prompt 前必须取得：

1. 用户提供的原视频。
2. 用户提供的字幕稿。
3. 榴莲大福产品参考图或已批准产品库条目。

缺少字幕稿时可以提取素材和分析原片，但不要决定人物讲话/画外音比例，也不要编译最终 Prompt。当前产品只使用 `durian-daifuku-v1`。

## 核心边界

1. 画面类型只允许 `product_showcase`、`person_product_showcase`、`person_eating`。
2. 声音方式按字幕段分别选择 `voiceover`、`on_screen_speech` 或 `silent`，不要预设全片画外音。
3. 人物吃产品时只能在咬前或咀嚼吞咽结束后讲话，禁止边咀嚼边说。
4. 先规划视频逻辑、产品展示/人物展示/人物吃产品占比和节奏，再写分镜 Prompt。
5. 每个分镜的 `source_first_frame` 必须是该分镜时间码的第一个真实帧。
6. `selected_beauty_keyframe` 是另选的美观参考帧，不能替代分镜首帧。
7. 只修改用户授权修改的元素；保持原构图时锁定人物、动作、机位、场景、光线和空间关系。
8. 不执行“审核即梦生成结果、差异返修 Prompt”流程。本 Skill 的检查发生在生成前：字幕策略、分镜结构、Prompt 完整性、节奏和商业权利。

## 1. 初始化

```bash
python3 <skill-dir>/scripts/init_project.py \
  --name <project-name> \
  --output <projects-directory> \
  --product-profile durian-daifuku-v1 \
  --style-profile ugc-food-review-v1
```

主要事实文件：

- `planning/story_plan.json`：字幕、声音方式、叙事逻辑、画面占比和节奏。
- `shots/shot_manifest.json`：逐镜事实、动作、声音、首帧和参考资产。
- `library/product_bible.json`：榴莲大福固定质感。
- `library/style_bible.json`：人物、场景、摄影和声音风格。
- `library/correction_memory.json`：长期纠错。
- `library/knowledge_index.json`：按镜头条件调用的 Prompt、规则和图片。
- `library/avatar_library.json`：以后补充的数字人及换脸授权资产。

字段见 [references/data-schema.md](references/data-schema.md)。

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

完整规则见 [references/prompt-rules.md](references/prompt-rules.md) 和 [references/workflow.md](references/workflow.md)。

## 5. 条件调用知识库

只调用 `library/knowledge_index.json` 中 `approved=true` 的条目。按 `visual_type`、`product_state`、`delivery_mode` 和 `narrative_role` 匹配 `applies_to`：

- `type=prompt` 或 `rule`：合并到对应镜头 Prompt。
- `type=image`：加入该镜头生成包的知识库参考图。

记录命中的条目 ID 和版本。不要把整个知识库无差别塞入每条 Prompt；冲突时以镜头硬约束、项目规则和产品规范优先。

## 6. 数字人库

用户以后补充人物素材时写入 `library/avatar_library.json`。每个数字人至少保存：

- 授权状态和使用范围。
- 正脸、左右 45 度、侧脸、不同表情和光线参考。
- 年龄呈现、发型、肤色、妆容、体型及禁止改动项。

未明确选择数字人或肖像权未确认时，不自动换脸。

## 7. 编译

```bash
python3 <skill-dir>/scripts/pipeline.py lint --project-dir <project-directory>
python3 <skill-dir>/scripts/pipeline.py compile --project-dir <project-directory>
```

修复所有 `ERROR` 后再编译。产出：

- `prompts/<shot-id>.md`：逐镜完整 Prompt。
- `prompts/generation_pack.json`：Prompt、三类帧、产品参考和知识库命中的映射。
- `review/shot_cards.md`：短分镜确认卡，包含画面/声音占比和节奏问题。
- `review/lint_report.json`：缺项、冲突和商业阻断。

聊天中优先交付短分镜确认卡；除非用户要求，不展开全部长 Prompt。

## 8. 工作台

UI 不替代 Skill，而是读写同一套 JSON 项目。架构和 API 边界见 [references/workbench-architecture.md](references/workbench-architecture.md)。

工作台至少提供：项目导入、字幕策略、故事结构、分镜时间线、首帧/美观帧选择、Prompt 检查、知识库、数字人库和生成包导出。所有关键决定必须可追溯到字幕段、原片时间码、知识库条目和规则版本。

## 9. 商业闸门

商业发布前读取 [references/commercial-gate.md](references/commercial-gate.md)。未清原视频、肖像、音乐、字体、产品声明和参考素材权利时，可内部测试但不能标记为可发布。
