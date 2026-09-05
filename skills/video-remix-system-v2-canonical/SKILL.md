---
name: video-remix-system-v2-canonical
description: Run a canonical, non-bypassable Jimeng commercial-video remix system for source-video analysis, exact source-frame product and packaging replacement, automatic role/speaker mapping, 20-second eating and two-tear planning, durian-daifuku state routing, strong human narrative Prompt compilation, image/text synchronization, QA, and DOCX delivery. Use when the user explicitly asks to test V2/Canonical/新版系统 or wants the fully automated recommended workflow with one source of truth and no direct-generation bypass.
---

# 视频改款系统 V2 Canonical

## 用途

用一个Canonical项目包驱动镜头、口播、角色、源帧、图片、Prompt和后续生成。任何正式生成只能从Canonical包编译，禁止直接把聊天Prompt交给图像或视频工具。`operation_mode`必须先锁定：`system_optimization`只修改系统并跑回归，`prompt_only`只交付Prompt，`full_delivery`才允许DOCX，`video_generation`才进入视频提交；PDF只作为内部临时渲染物。

## 必读

完整读取 [references/canonical-contract.md](references/canonical-contract.md)。榴莲大福任务还必须读取活动版 `$durian-daifuku-five-states` 的产品规范、批准资产和视频状态机；DOCX使用 `$documents`。

## 唯一入口

1. 创建或更新 `canonical_project.json`，其中必须填写 `semantic_role_performance_gate_file` 并通过角色、动作、口播和表演门禁，再运行：
   `python3 scripts/canonical_pipeline.py compile <canonical_project.json> <build-dir>`。
2. 编译器会强制读取并校验语义门禁；只允许使用编译结果中的 `semantic_role_performance_gate.json`、`final_generation_manifest.json`、`script_shot_map.json`、`image_task_manifest.json`、`prompt_task_manifest.json` 和 `rule_receipt.json`。
3. 没有有效收据、源帧哈希或任务manifest时，不得正式调用生图、视频生成或DOCX导出。

源视频交接与改稿使用 `scripts/source_handoff.py`：先把已完成的源片转录规范化为绑定源视频哈希的 `source_intake.json`，交给用户确认/修改；用户交回完整新版口播后生成 `revision_impact.json`。line ID集合不完整、源视频哈希改变或已有Prompt/图片受影响时，旧语义门、Prompt、图片状态和DOCX均进入过期状态，必须重新编译，不得只改 `script_shot_map` 正文。

## 自动工作流

1. 按源片硬切建立镜头；连续运镜自动合并。原子帧只是同镜状态，不按图片数量拆镜。`generation_shots`必须携带有序`source_shot_ids`、源帧哈希和`shot_mode`；图片数量不能生成新的S编号。
2. 自动识别人物数量；单人物建立一个角色，多人物建立稳定ID，画外声只在有证据或用户指定时建立。
3. 完整口播拆成line ID；每个line ID必须在Canonical中显式绑定镜头和声源，系统不按容量猜测去向。每个line ID恰好一次，叙事段只引用ID或概述。
4. 吃食目标按时长计算：低于20秒不强行补入；达到20秒后为`max(源片已有有效事件数, 3 + floor((目标时长-20)/10))`。源片和已批准示范中的有效入口全部保留，只补缺口；补入事件绑定既有源帧和既有S镜头，不新建伪造S编号。双手掰开目标为`max(源片已有有效过程数,2)`。
5. 纸箱、纸盒和袋子在原帧区域替换为批准榴莲大福包装体系。
6. 每个图像任务生成锁定区域、替换区域、尺度锚点、产品状态、包装状态、参考资产角色和哈希收据。正式生图不得绕过任务manifest。
7. Prompt分两阶段：先编译事实动作脚本，再增强为强烈活人感叙事。人物、纯手部、微距和纯产品按`shot_mode`分流。启用源片表演证据合同时，人物镜头必须从原片记录的视线轨迹、眉眼微反应、肩线/重心、双手职责、声音观察和情绪落点写出连续因果，Prompt还必须回写源片动作锚点；情绪不能靠固定形容词或重复段落代替。负面限制不超过必要范围，复制区不输出内部“触发”字段。
8. 自动QA后最多自修复两轮。只对无法消解的说话人、屏内/画外或身份参考冲突询问一次。
9. DOCX只负责排版Canonical结果，不决定镜头、台词或资产；没有视频后端、凭证或返回结果时，状态停在`BLOCKED_VIDEO_BACKEND`，不把DOCX/PDF称为视频交付。

DOCX 只能由 `scripts/export_docx_from_build.py` 从同一编译目录读取五份清单导出。它会逐镜核对当前口播、Prompt文件哈希、批准/生成QA图和图片哈希；任一镜头缺图、仍为`awaiting_generation`、图文状态不符或构建过期时直接阻断，不写出部分完成文档。`rewrite_docx_v3.py` 等项目历史脚本不是V2入口。

图像生成后端

V2 的静态图/首帧层默认使用 Seedream 5.0 Pro。通过 `canonical_project.json` 的
`image_generation_provider` 配置模型；编译器会把该配置写入 `image_task_manifest.json`
和 `rule_receipt.json`。Seedream 负责静态产品图、源帧局部替换和视频首帧，视频模型不在
此图像层直接调用；正式 API 提交必须由经过验证的 provider 执行器消费编译产物。

已提供 `scripts/seedream_ark.py` 作为 Ark 执行器：先完成 compile/validate，再设置
`ARK_API_KEY`，执行 `python3 scripts/seedream_ark.py submit <build-dir> <result.json>`。
`--dry-run` 可在不联网的情况下检查任务数量和 provider 配置。

## 硬门与软门

硬门：源帧哈希、镜头覆盖与顺序、line ID显式唯一、声源、产品状态、尺寸、包装、按时长的吃食/掰开数量、shot_mode角色边界、Prompt文件哈希和图文manifest一致。编译不再吞掉旧哈希或自动猜口播归属；硬门失败直接阻断错误交付。

软门：情绪强度、叙事张力、微表情、运镜落点、水词和重复形容词。软门自动重写，不向用户请求审批。
