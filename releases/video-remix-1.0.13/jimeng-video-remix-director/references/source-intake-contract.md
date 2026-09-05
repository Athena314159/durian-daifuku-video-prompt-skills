# Source intake 阶段合同

## 1. 阶段选择

在创建图文任务前运行：

```bash
python3 <skill-dir>/scripts/resolve_launch_contract.py \
  --source-video-provided \
  --dual-agent-requested \
  --product-directive unspecified
```

只在以下条件同时成立时进入 `full_delivery`：

1. 用户明确要求完整首帧、逐镜 Prompt 与 Word；
2. 新版口播已经由用户确认并锁定；
3. `product_mode=preserve_source_product`，或用户明确要求换品且批准目标产品参考已经绑定。

其他有源视频的情况先进入 `source_intake`。用户只说“图文 Agent 帮我跑一下这个视频”不是完整交付授权。缺源视频时用 `awaiting_source_video`，不创建空跑分支。

`product_directive=unspecified|preserve` 都解析为 `product_mode=preserve_source_product`。只有用户明确说换产品、改款到某产品或同义指令时才解析为 `replace_product`。`replace_product` 缺参考时把 `target_product_reference` 放入 `pending_inputs`；它是下一阶段待输入，不是本阶段 blocker，也不能阻止提取原片口播或原片视觉清单。

## 2. Source intake 双分支

Source intake 不需要最终 locked shot map。总控先保存只读 `source_intake_contract.json` 及其 SHA-256，再给两个分支互不重叠的写入目录：

- 图线：只做原片视觉分镜/动作切点清单、真实首帧与可见字幕证据定位；保留原片人物、产品和包装，不换主体、不生图、不把源帧称为批准生成帧。把通过解码、时间戳、尺寸和可见事实核验的源帧称为“已核验源图”，并按本合同直接展示。
- 文线：提取可见字幕、自动检测语种、结合音频/口型形成带时间码的原片口播；不能等用户先给新版口播，不能按产品名、品牌名、国名或产地名决定语种。

公共信封至少写入：

```text
branch_role=image|text
may_create_threads=false
execution_tier=source_intake
product_mode=preserve_source_product|replace_product
source_video_path=<绝对路径>
source_intake_contract_path=<绝对路径>
source_intake_contract_sha256=<64位 SHA-256>
branch_write_root=<分支独占绝对路径>
required_handoff_path=<分支 handoff JSON 绝对路径>
```

两线 handoff 使用 `references/schemas/source_intake_handoff.schema.json`，并运行：

```bash
python3 <skill-dir>/scripts/validate_source_intake_handoff.py \
  --handoff <source-intake-handoff.json>
```

图线达到 `source_inventory_ready` 时，`source_inventory.source_shot_ids[]` 是必须完整覆盖的 SRC 集合，`source_shots[]` 中每个 SRC 必须提供：唯一 `source_shot_id`、数值型 `timecode.start/end/duration`、可读 `caption`、指向实际存在文件的绝对 `image_path`。图线还必须写入：

- `controller_reply.must_inline_images=true`；
- `controller_reply.may_only_report_path=false`；
- `controller_reply.deliver_when_ready=true`。

校验通过后立即运行：

```bash
python3 <skill-dir>/scripts/render_source_gallery.py \
  --handoff <source-intake-image-handoff.json>
```

把脚本输出的全部 `![SRC…｜准确时间码｜caption](/absolute/image/path)` 原样贴入图分支的用户可见回复；总控收到后也应直接内联，不得只给 handoff、图库目录、overview 拼图或绝对路径文本。每一张 SRC 图片准备好后即可展示，不等待目标产品、新版口播、另一分支或完整交付阶段。

## 3. 产品模式和等待状态

`blocked` 只用于无法继续本阶段的真实技术失败、事实冲突或验证失败，例如源视频损坏不可读、同一时间段出现无法消解的互斥源证据、写入失败或结构校验失败。此时每个 `blocked_items[]` 必须包含 `kind=technical_error|fact_conflict|validation_failure`、稳定 `code` 和可观察 `evidence`。

普通下一阶段输入不得写成 `blocked`：

- 原片口播已提取：文线用 `status=transcript_ready`；仍等用户修改时同时写 `pending_inputs=["revised_script"]`，不降低完成状态。
- 原片视觉清单完成：图线用 `status=source_inventory_ready`；明确换品但缺参考时同时追加 `target_product_reference`。
- `pending_inputs` 描述下一阶段还需要什么，不代表当前 source intake 失败或没做完。
- `status=awaiting_inputs` 只兼容“本阶段确实还无法产出可用结果、但等待普通输入即可继续”的情况；已得到口播或视觉清单时优先使用对应 `*_ready` 状态。

`preserve_source_product` 时禁止出现 `target_product_reference` 待输入，也禁止要求产品库绑定。

缺目标产品、目标人物参考、换脸授权或商业发布授权都不得阻止 source intake，也不得写入错误型 `blocked_items`：本阶段只盘点原片可见事实，不实施换品、换脸或商业发布。只有用户明确要求换品时，才按 schema 把 `target_product_reference` 记为下一阶段 `pending_inputs`；人物/授权需求留作下一阶段闸门说明，不得把已经完成的源图或口播降级。

## 4. 语种与原片口播

语言决策只允许以下证据，按优先顺序记录：

1. `visible_subtitles`：逐秒可见字幕；
2. `automatic_language_detection`：对有效语音段自动检测；
3. `speech_audio`：ASR 候选与听辨；
4. `lip_reading`：只作辅助。

`product_name`、`brand_name`、`country_name`、`origin_label` 必须记录在 `language_detection.excluded_signals`，不得出现在 `evidence_used` 或 `decision_source`。看不清或听不准的字写成 `[待核 00:12.30–00:13.10]`，不补猜词。

文线 handoff 必须包含：

- `transcript.source_language`；
- 连续、可复制的 `transcript.editable_text`；
- 带起止秒数、文字、证据和置信度的 `transcript.segments[]`；
- `controller_reply.must_inline_editable_text=true`；
- `controller_reply.may_only_report_path=false`；
- `controller_reply.deliver_before_other_branch_complete=true`。

## 5. 用户可见的口播与可点击图库

一旦文线 handoff 校验通过，文分支先在自己的用户可见任务直接贴出 `transcript.editable_text`；总控随后不等待图线、不等待目标产品参考，在当前用户任务再贴出同一正文。两处都不能只报路径。回复结构只有必要内容：

```text
原片口播（可直接修改）：

<连续正文；待核位置保留时间码>

请直接在这版上修改或确认；确认后我继续锁定分镜、图文合并并按你要求交付。
```

禁止只回复“已提取”“handoff 为 partial/complete”“路径见 JSON”，也禁止要求用户自行打开内部文件才能看到口播。路径和 handoff 只供总控内部校验。

用户如果在文分支直接提交修改稿，文分支把正文写回自己的 handoff，并仅用 `send_message_to_thread` 向信封中的既有 `controller_thread_id` 发送正文、状态与新 handoff 哈希；不得建任务或 fork。若当下无法解析总控 ID，保留 handoff 并告诉用户总控会通过后续监控快照接收，不得形成“请回另一个对话再贴一次”的死胡同。

图分支遵守以下可见交付合同：

1. 首张源帧通过解码、准确 PTS、尺寸和画面事实核验后，立即在 commentary 直接嵌入，不要等整批完成。使用绝对本地路径和可点击 Markdown，例如 `[![SRC001｜00:00.000｜已核验源图](</绝对路径/SRC001.png>)](</绝对路径/SRC001.png>)`。纯路径、代码块、JSON、handoff 链接或“已完成 N 张”都不算展示。
2. 在约 25%/50%/75%/100% 完成度跨点更新。每次只嵌入自上次更新后新增的已核验图片；图片很多时可改发一张可点击联系表。每个进度更新都必须含图片，禁止只说百分比、状态或路径。联系表只用于进度概览，不能替代最终逐张图库。
3. 阶段最终回复按编号稳定排序，先 `SRC001…`，再列合同确实存在的 `ADD001…`；source intake 不建立 ADD，因此通常只列 SRC。为每个编号直接嵌入其本地图片并标注编号与准确时间，不能只给目录、文件名或 handoff。不得用一张拼图冒充最终逐张图库。
4. 把上述图片视为用户可见的阶段进度，不把它们写进最终用户输出目录。后续进入 `full_delivery` 时，整个项目的最终交付仍只提供唯一 DOCX。

总控收到并校验 `status=source_inventory_ready`（或后续阶段的 image-ready handoff）后，立即在当前用户任务按相同顺序镜像可点击图库，不等待文线、口播修改、目标产品、目标人物或授权。总控可直接使用 handoff 中已核验的绝对图片路径生成 Markdown；不得只转述分支状态或 handoff 路径。若 handoff 声称图片完成但所列文件不存在或不可读，才按真实技术/验证失败定点退回图线。

总控收到用户修改稿后，把它锁为 `revised_script_lock`。若此时已明确完整交付且产品模式也满足条件，再重新解析合同并进入 `full_delivery`；不要重做已经完成的 source intake。
