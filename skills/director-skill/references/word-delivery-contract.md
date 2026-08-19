# 即梦逐镜视频 Prompt Word 交付契约

适用于用户指定的 `355e7016_达尔顿黄油脆丝棒_即梦逐镜视频Prompt_3000字长版.docx` 的交付形态。该参考文档是版式与信息架构依据，不继承其中的人物、产品、口播、镜头或商业声明。

## 固定结构

1. 封面：项目 ID、产品名、Prompt 标题、镜头范围、3000–4000 字约束、日期；放置所有批准首帧的 3 列缩略图总览。
2. 项目总览：上传顺序、生成方式、角色/换脸范围、儿童或非目标人物保护锁、无字幕无水印规则、产品和包装不变量、镜头清单表。
3. 每镜一页：`S编号`、标题、独立时长、画面类型、人物/声源、批准首帧、口播、参考图与编辑范围、产品中文状态、提示词字符数、淡蓝色“即梦可复制 Prompt”区域、原片动作对应、内容审核记录。
4. 收尾页：镜头—首帧—Prompt—生成结果回填表与仍需像素复核项。

## 首帧与参考图规则

- 每镜图片只能使用 `approved_generation_first_frame`；它必须来自该镜 `source_first_frame`，不是美观关键帧。
- 节奏返工、重拆镜或重做 Word 前，先读取 `planning/asset_reuse_plan.json`。历史批准帧在人物、产品状态、包装数量、场景、构图、动作语义、画幅和像素 QA 匹配时优先复用，禁止仅因 S 编号或目录变化重新生图。
- 用户明确要求复用历史动作/美观候选帧时，必须先把它以新资产 ID 提升为批准帧，记录原资产 ID、旧/新 S 编号、用户授权、QA 结论和仅做的确定性画幅整理；不得静默把未批准候选当批准首帧。
- 换脸镜必须列出目标人物参考 ID、肖像授权范围和保护对象；未授权则写“未启用换脸”，并保持原人物。
- 裸产品镜必须列出实际绑定的产品细节图；包装镜必须列出包装参考图。图片承担的职责必须明确，不能把参考图背景/构图误带入镜头。
- 人物与产品同时替换时，Word 同镜列出 avatar/face 资产 ID、product 资产 ID、各自编辑范围和交叉像素保护区；整图批准不能替代两个库层分别审核。
- 所有图片和 Prompt 以相同的 S 编号相互链接。任何首帧重新审核或返工后，必须重新导出 Word，禁止只替换 Word 图片。

## Prompt 与 QA 硬门

- Word 中的可复制 Prompt 逐字读取 `prompts/<S编号>.md` 的代码块；生成后计算去空白字符数并显示。
- `prompts/<S编号>.md` 的 `text` 代码块是唯一 Prompt 事实源。总 TXT、逐镜 TXT 和 Word 均由它派生，禁止分别编辑。导出后运行 `scripts/align_exports.py --require-docx`，逐镜核对 Prompt SHA-256、Word 精确文本、批准帧文件哈希和 DOCX 实际内嵌媒体哈希。
- 每镜只能在 Prompt 通过项目 lint 后导出；镜内时间从 0.00 秒开始。
- 导出完成必须使用 Documents Skill 渲染、逐页审看；检查中文字体、图片、页码、首帧和 Prompt 区域无裁切、无重叠、无乱码。
- 导出前以 `--stage pre-word` 运行 `scripts/audit_asset_reuse.py`；Word 内嵌图片数必须等于 `summary.expected_word_image_count`。逐张检查为独立9:16、无源字幕/水印、主体未退回原视频，且未用拼图、重复图或参考库图片冒充分镜。
- 输出旁必须保留 `exports/<stem>.manifest.json`，记录 source-first-frame、approved-first-frame、avatar/product references、canonical Prompt SHA-256、Word SHA-256、导出时间、`reused_frame_count`、`new_generation_count`、图片来源资产 ID、旧/新 S 编号、哈希、尺寸和补生理由。`review/alignment_manifest.json.summary.status` 不是 `aligned` 时不得交付。
