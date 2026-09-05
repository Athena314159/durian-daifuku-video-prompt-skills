# V3 Canonical 小白操作说明

这份说明只讲“如何把一条源视频整理成可审计的 V3 项目包”。它不要求你会写代码，也不会把未接入的视频后端假装成已经出片。

## 先记住三件事

1. **Seedream 只负责静态图和视频首帧。** 当前执行器走火山引擎 Ark，默认模型 ID 是 `doubao-seedream-5-0-260128`。它可以处理源帧局部换产品、包装替换和首帧图，但不是视频生成器。
2. **视频后端目前未接入。** 编译和静态图提交通过后，状态最多到 `READY_FOR_SUBMIT`；没有视频 provider、凭证和返回成片时，状态是 `NOT_REQUESTED` 或 `BLOCKED_VIDEO_BACKEND`。
3. **所有正式任务都从一个 `canonical_project.json` 编译。** 不要把聊天里的 Prompt 直接复制给生图或视频工具，也不要用图片数量猜镜头数量。

## 目录里最重要的文件

- `scripts/canonical_pipeline.py`：编译和校验项目。
- `scripts/seedream_ark.py`：把已经通过校验的静态图任务提交到 Ark/Seedream。
- `scripts/export_docx_from_build.py`：把已生成且带 QA 哈希的 Canonical 结果排版成 DOCX，并输出对齐收据。
- `scripts/self_test.py`：检查 V3 系统本身是否正常。
- `assets/canonical_project.template.json`：项目配置模板。
- `references/canonical-contract.md`：完整规则，遇到冲突以它为准。

## 第一步：准备运行环境

下面的 Python 路径是 Codex 工作区常用的运行时；如果你的电脑没有这个路径，使用 Python 3.10 或更高版本即可。

```bash
PY=/Users/apple/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
```

先检查系统：

```bash
"$PY" scripts/self_test.py
```

看到 `V2 SELF TEST PASSED` 才继续。这个测试字符串为兼容旧自动化而保留；自测失败时不要提交生图任务，先看报错指出的规则。

## 第二步：复制项目模板

复制 `assets/canonical_project.template.json`，另存为你自己的 `canonical_project.json`。至少修改这些字段：

- `project_id`：项目唯一名称。
- `source_video`：源视频绝对路径。
- `target_duration_seconds`：最终目标时长。
- `operation_mode`：新手建议先用 `prompt_only`；只改系统时用 `system_optimization`。
- `semantic_role_performance_gate_file`：角色、动作、口播和表演证据文件的绝对路径。
- `source_coverage.required_source_shot_ids`：源片实际镜头 ID，按原片顺序填写。
- `script_lines`：完整口播，每句一个 `line_id`。
- `generation_shots`：正式生成镜头，必须使用 `S001`、`S002` 这种 ID，并明确对应哪些 `SRC` 镜头。

### 每个生成镜头必须写清楚

每个 `generation_shots` 项都要有：

- `source_shot_ids`：它来自哪一个或哪些连续源镜头；顺序不可变。
- `shot_mode`：只能是 `person_visible`、`hands_only` 或 `product_macro`。
- `source_frame` 和 `source_frame_sha256`：实际源帧路径和 SHA-256。
- `line_ids`：本镜使用哪些口播 ID；必须显式绑定，系统不会按容量猜。
- `prompt_file`：本镜唯一 Prompt 文件。
- `source_state_contract`、`product_state`、`bbox_xywh` 和 `scale_anchors`：产品状态、替换区域和比例锚点。

### 人物表演证据怎么写

新项目模板启用了 `performance_contract_version: "2.1-source-evidence"`。人物镜头必须在项目证据文件里记录真实源片观察：

- `gaze_path`：视线先落在哪里，再回看哪里，停留多久。
- `facial_micro_reactions`：实际看见的眉峰、上眼睑、嘴角或下巴变化。
- `shoulder_weight_shift`：肩线、重心或身体前倾/回收。
- `hand_roles`：每只手在源片里负责什么，何时换位。
- `voice_observation`：原声的音高、语速、重音、尾音、语气词；没有可辨原声时要明确写“创作提案”。
- `emotion_landing`：情绪落点如何改变后一个动作或视线。
- `source_anchor_terms`：能在 Prompt 里找到的源片动作锚点，至少两个。

纯手部镜头和微距断面镜头只写它们能被镜头看见的内容：手指受力、重量、包装摩擦、焦点、景深、冰皮和果泥的变化。它们不填写人物脸部表演。

## 第三步：编译和校验

```bash
"$PY" scripts/canonical_pipeline.py compile \
  /绝对路径/canonical_project.json \
  /绝对路径/build

"$PY" scripts/canonical_pipeline.py validate \
  /绝对路径/build
```

只有 `rule_receipt.json` 的 `status` 为 `PASS` 才能进入静态图提交。编译器会自动检查：

- 源镜头顺序和源帧文件是否真实存在；
- SHA-256 是否和文件内容一致；
- 口播 line ID 是否每句恰好绑定一次；
- 吃食数量是否符合时长阶梯：低于 20 秒不强行补，达到 20 秒后目标为 `3 + floor((时长-20)/10)`，但始终保留源片已有事件；
- 双手掰开目标是否至少 2 次；
- `shot_mode` 是否与 Prompt 内容匹配；
- Prompt 文件、编译结果和收据是否为同一版本。

## 第四步：先做 Seedream 干跑

不联网检查任务数量和模型配置：

```bash
"$PY" scripts/seedream_ark.py submit \
  /绝对路径/build \
  /绝对路径/image_result.json \
  --dry-run
```

干跑显示 `PASS` 后，才考虑正式提交。正式提交前把密钥放在环境变量里，不要写入 JSON、Prompt 或 Git：

```bash
export ARK_API_KEY='你的火山引擎 Ark Key'
"$PY" scripts/seedream_ark.py submit \
  /绝对路径/build \
  /绝对路径/image_result.json
```

执行器会读取 `image_task_manifest.json`，逐任务调用 Ark 图片接口，并写出结果 JSON。密钥不会从项目文件读取。

## 失败状态怎么处理

- `BLOCK_MISSING_SOURCE_FRAME`：源帧路径错误，或文件不存在。
- `BLOCK_SOURCE_FRAME_HASH`：重新计算该帧 SHA-256，修正项目文件。
- `BLOCK_UNMAPPED_LINE`：给每个 `line_id` 明确填写归属镜头。
- `BLOCK_SHOT_ORDER`：检查 `source_shot_ids` 是否和原片顺序一致；不能用新增 S 编号掩盖漏镜头。
- `BLOCK_PROMPT_ROLE`：人物、纯手部、微距镜头写入了不属于它的表演内容。
- `BLOCK_STALE_BUILD`：Prompt 或项目改过，必须重新 `compile`，不能复用旧 build。
- `BLOCKED_VIDEO_BACKEND`：静态图规则已通过，但当前没有可用视频 provider；到这里停止，不把 DOCX/PDF当作视频结果。

## 最安全的工作顺序

源视频观察 → 建立 SRC 镜头表 → 为每个正式镜头锁定源帧和哈希 → 明确 line ID → 写人物/手部/微距证据 → 编译 → 校验 → Seedream 干跑 → 有凭证时提交静态图 → 接入视频 provider 后再单独提交、等待、校验视频。

任何一步失败都只修对应的镜头或字段。不要为了“让数量看起来对”新增没有源片依据的镜头，也不要把原视频图片重新塞回已经批准的生成图任务。

## 第五步：导出 DOCX（必须先有 QA 图）

DOCX 导出器只消费 `build` 中的五个 Canonical manifest。每个 `selected_shots` 必须同时有当前口播映射、当前 Prompt，以及 `user_approved` 或已生成的 QA 图和对应 SHA-256。`awaiting_generation`、缺图、哈希不一致，或 Prompt/口播映射过期，都会在写入 DOCX 前硬阻断。

使用工作区自带的 Python 运行时（已包含 `python-docx`）：

```bash
"$PY" scripts/export_docx_from_build.py \
  /绝对路径/build \
  /绝对路径/delivery.docx \
  --alignment-manifest /绝对路径/alignment_manifest.json
```

成功时会为每个镜头按同一顺序写入口播、Prompt 和 QA 图，并生成 `alignment_manifest.json`，记录每一项的文件路径、哈希和 DOCX 段落位置。导出失败不会留下可被误认为完整交付的 DOCX。
