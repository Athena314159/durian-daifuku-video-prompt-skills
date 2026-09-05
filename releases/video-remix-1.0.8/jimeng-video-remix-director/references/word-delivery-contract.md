# 即梦逐分镜 Word 唯一交付契约

用户侧唯一交付文件是渲染检查通过的 `.docx`。内部 canonical Prompt、TXT、JSON、manifest、对齐报告、图 Agent handoff 和文 Agent handoff 继续用于机器校验，但不得作为用户附件、不得在最终回复中逐项罗列。禁止用“对齐表格”、调试报告或聊天说明代替 Word。

## 固定结构

1. 封面：项目名、目标产品、原片 `SRC` 数、有依据新增 `ADD` 数、连续生成段 `S` 数与 Prompt 长度硬门状态。封面不放缩略图图库，避免封面图片被误算成正文目标帧。
2. `当前结构结论`：用简短可编辑文字说明 SRC 全保留、短镜只合并不删除、ADD 来源、经用户总览确认的目标帧总数、口播/吃食/掰开/包装执行信息均在正文，以及用户最终只接收这一份 Word。六层审计留在内部 manifest，不在正文占栏。
3. `生成段总览`：只汇总 `S`、其包含的 `SRC/ADD`、整段准确时间和主要职责；它不是内部 alignment/handoff/QA 表。
4. 每个生成段使用标题 `S编号｜按生成顺序排列的 SRC/ADD｜MM:SS.mmm–MM:SS.mmm`，并写连续生成总时长、画面类型、叙事作用及必要合并理由。
5. 每个 `S` 的 `动作镜头对应` 必须按生成镜内时间列出该段全部 `SRC/ADD`，不得先按 SRC、后按 ADD 改变真实顺序。每个动作镜头都包含：
   - `SRC` 的原片准确起止秒数和生成镜内准确起止秒数，或 `ADD` 的“无原片秒数”与生成镜内准确秒数；
   - 按原片节奏和新版口播推算的可编辑文字版分镜描述；
   - 该镜对应的可编辑新版口播；确实无声时明确写“无”；
   - 不显示内部六层字段；只显示已经融合进逐时动作和 Prompt 的可拍表演结果；
   - 对应的吃食节奏证据、掰开酥脆证据和包装可见面证据；没有事实时不虚构；
   - `ADD` 额外保留新增理由、节奏锚点、真实源片参考 ID 与参考帧，禁止编造原片秒数。
6. 每个 `S` 的 `目标帧与职责` 按 unit 顺序展示所有经用户总览确认的图片。每个 `SRC/ADD` 至少1张；同一 unit 出现多个连续动作关键状态时可有多张，并严格按动作顺序排列。每张图下必须写 `unit ID｜asset ID｜用户已确认｜职责：可编辑职责`，图片与说明不能拆到别的 unit。
7. 每个 `S` 的 `可复制Prompt原文` 逐字来自同一 `compile_id` 的 canonical `prompts/<S编号>.md`，不在 Word 中另写一版。文末放最终交付检查清单。

不再放“生成结果回填表”“对齐状态表”“分支交接表”“内容审核 PASS/FAIL 表”或空白流程页。用户要求的是可直接使用的完整 Word，不是内部过程证明。

## 原片分镜不丢失

- `source/source_manifest.json.source_shots` 是原片原子分镜事实源；第一镜从0开始、相邻边界无空洞或重叠、最后一镜抵达 ffprobe 时长，并以源帧索引/半帧容差验收。
- `shots[].source_units` 必须按原顺序覆盖全部 `SRC`，每个恰好一次。
- 原片小于4秒时，把相邻 `SRC` 合并到同一 `S`；Word 仍把它们作为独立分镜卡逐个显示，秒数、描述、口播和图片都不能合并丢失。
- 每个 `SRC` 和 `ADD` 的 `delivery_asset_ids` 都必须是非空有序数组；同一 unit 可保存多张不同动作关键状态图，多图时每张都必须有互不重复的 `delivery_asset_roles`/responsibility。资产 inventory 的 provenance 必须显式包含真实 owner unit。
- 不同 unit 默认禁止复用相同 asset ID、解析后路径或实际 SHA-256；任何资产都不能冒充另一 unit。只有相邻生成段边界为了连续性可把 owner unit 的同一资产再显示一次，且必须显式登记 `continuity_boundary_reference=true`、真实 `owner_unit_id`、asset ID 和边界职责；它仍归原 owner，不计入当前 unit 的最低图片覆盖，也不能改写 caption 归属。
- `shots[].inserted_units` 只保存 `ADD…`；每项有唯一 ID、生成镜内时间、描述、口播、独立批准图、新增理由、节奏锚点、真实源片参考 ID/帧。
- 把同一 S 的 `source_units + inserted_units` 按 `generation_timecode` 排序后，必须从0.00连续覆盖整个 `S`，无空洞、无重叠。S 可以只有 SRC、只有 ADD，或同时包含两者。

## 吃食与掰开在 Word 中的表达

- 原片总时长达到30秒时，Word 中的吃食次数为 `max(原片已有次数, 3)`；原片已有3次就不新增，少几次只补几次。新增点以 ADD 明示，必须分散，中间隔开非吃食节奏，不排成连续三镜。
- 吃食分镜描述来自原片可见动作和表演：送入口、张口、牙齿接触、产品离嘴、咬口、视线、头部节奏和声音。模板只补有依据的缺口。
- 咬合完成、产品离嘴后可以马上接该镜口播。原片没有吞咽或吃后反应时，Word 不得写出一个虚构的吞咽/满足反应。
- 黄油脆丝棒必须至少有一个人物不出镜的纯手部掰开镜，同时可按节奏安排人物出镜掰开镜。Word 描述和掰开证据块须写出：绑定 SRC/ADD、镜内准确秒数、节奏理由、同一根一次咔嚓、真实断点、3–8片克制掉渣、互补橙金断面、两段质量守恒和音画同步。只有 metadata、但分镜描述/动作节拍/产品变化/拟音没有实际掰开语义时仍阻断。

## 包装图案完整性

- Word 中每个包装镜按 `box_id` 列出正面、侧面、顶面三行；每面写 `visible/occluded/hidden`、可见多边形/面积、是否达到可读阈值、对应母版、预期图案/文字检查点和真实不可见原因。
- 允许盒子正常出框、被手或前景遮挡；这些区域记录为自然不可见，不算“图案不完整”。
- 所有实际可见区域必须与母版一致：文字不缺、不乱码、不镜像，图案不压缩、不重排、不跨折边错位。原尺寸候选裁切必须绑定该 SRC/ADD 正文批准目标帧之一的 asset ID、父图 SHA-256 和裁切坐标；旧候选或其他图片的 crop 不能充当证据。
- 需要可读包装文字时，先用母版单应性或确定性合成投射到盒面，再只处理盒体透视、纸面反光、场景光、接触影和边缘融合。禁止让模型重画印刷，也禁止为了露全图案缩小或改版。
- 在调用图片模型完成盒体几何后、最终批准前，使用确定性母版投射工具；`--quad` 顺序固定为左上、右上、右下、左下，手部遮挡或自然出框用与输出同尺寸的灰度 `--visible-mask` 保留：

```bash
python3 <skill-dir>/scripts/project_package_master.py \
  --candidate <box-geometry-candidate.png> \
  --master <approved-front-side-or-top-master.png> \
  --face <front|side|top> \
  --quad <x1,y1,x2,y2,x3,y3,x4,y4> \
  --visible-mask <optional-visible-mask.png> \
  --output <projected-approved-candidate.png> \
  --manifest <project-directory>/review/<unit>-<face>.projection.json
```

  投影 manifest 是包装 QA 的内部证据，至少核对 schema、face、candidate/master/output 的 SHA-256 与尺寸、目标四边形、遮挡 mask、`projection_method=homography` 和 `model_redraw_used=false`；它不写入用户 Word，也不能只凭 manifest 跳过对最终图的可见文字、方向、跨棱和遮挡检查。

## Prompt 与图片硬门

- Word 可复制 Prompt 逐字读取当前 `compile_id` 对应的 `prompts/<S编号>.md` `text` 代码块；导出前重跑 lint，并核对 canonical input hashes、当前 Prompt 文件哈希和 `prompts/history/<compile_id>/input_snapshot.json`。发现旧编译或混批时必须阻断重编译。字符数只读取 `project.json.prompt_length_contract`；启用时上下限同时为硬门，关闭时两者都不检查。
- Word 内每个 `SRC/ADD` 必须嵌入其完整、有序、自有、已获用户确认的目标帧数组；参考图、source-first、美观候选、拼图、仅 Agent 自审图和未批准生成图不能直接进入 Word。导出前 `gallery_receipt` 必须绑定用户实际看过的全部图片顺序和 SHA-256，每张图片的 `user_approval` 必须绑定同一回执。内嵌 owner 图片数必须等于全部 unit 的 `delivery_asset_ids` 总长度；另加的连续边界参考单独计数。
- 锁定新版口播全文与程序实算字符数。按生成时间拼接 Word 内全部 SRC/ADD 的可编辑口播，再按 S 顺序拼接全部有声 Prompt 口播，两份都必须与锁定全文等价；缺字、重复、用文字截图代替编辑文字或手填虚假字数均阻断。
- 任何吃食、掰开、包装、人物身份或产品结构失败都只退回对应 `SRC/ADD/S`，不得用错误图占位后先交 Word。撤销一张已批准图片时必须运行 revocation cascade，把 workflow、generation pack 和 export manifest 标为 stale；补图后重新展示全量更新总览、取得新回执、重编译和重导出。
- 导出后内部运行 `scripts/align_exports.py --require-docx`；该脚本只读 canonical/pack/DOCX，不派生 TXT、不回写 pack。它从 OOXML 正文顺序读取生成段 Heading、`动作镜头对应`、`目标帧与职责` 与可编辑标签，并沿每个 `<a:blip>` relationship 读取实际 media bytes，把 caption 中的 owner unit、asset ID、职责、出现顺序和 SHA-256 与 canonical/manifest 的完整有序数组逐项比较。缺其中一张、顺序颠倒、caption owner 错写、图片字节互换或跨 unit 冒用均阻断。Prompt SHA-256、编译快照、秒数、分镜描述、口播、内部六层 manifest 完整性且正文无“六层证据”栏目、`SRC/ADD` 顺序、selected asset ID 或项目唯一 `prompt_length_contract` 任一不一致也阻断。
- 最终必须用 Documents Skill 渲染并逐页检查：中文字体、准确秒数、分镜描述、口播、图片、Prompt 均无裁切、重叠、空白页或乱码。渲染通过后才把 DOCX 复制到用户输出目录；该目录只保留这一份 DOCX。
