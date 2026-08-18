---
name: durian-daifuku-five-states
description: Replace the main food product in an existing image with a physically consistent approximately 7 cm durian daifuku, or generate, edit, and validate its five product states and video transitions. Use when Codex needs to 换主体、换食品、把原产品换成榴莲大福、改图、生成榴莲大福图片、人物吃大福、双手撕大福、横截面、两半满馅、即梦视频 Prompt、跨人物跨场景产品一致性、中文生图 Prompt、参考图角色控制或严格像素 QA.
---

# 榴莲大福主体替换、五形态与视频连续性

把当前版本视为内部待审核修复稿。用户明确批准前，不得称为正式版或写入正式产品知识库。

## 必读路由

根据任务只读取必要资源，但必须完整读取所选文件：

- 所有任务先读 `references/product-spec-and-reference-roles.md`。
- 任务使用用户自备图片、视频关键帧或包装盒时，再读 `references/user-media-reference-map.md`；按其中的角色边界选择资产，不能把动作参考当产品外观母版。
- 用户要求把图片中的主体食品换成榴莲大福时，再读 `references/image-subject-replacement.md` 和当前一个状态模块。
- 生成或编辑静态五形态时，再读 `references/five-state-prompts-v8-pending.md`；只加载公共基底与当前一个状态/终点。
- 生成人物吃、双手撕或其他连续视频 Prompt 时，再读 `references/video-state-machine.md`。
- 历史描述发生冲突或准备恢复旧词时，读 `references/historical-rule-status.md`；已否决规则绝不复活。

## 任务路由

- 完整、摆盘、端盘、手拿未破产品：V1 `whole/held/plated`。
- 人物咬、拉开、断裂、回缩、咀嚼、吞咽：V2 `person_eating`。
- 双手从完整产品开始按压、建立撕口并拉开：V3；按请求选择 `opening_window`、`pre_break` 或 `break`。
- 单个断面朝镜头：V4；先识别来源 `hand_torn`、`bitten` 或明确要求的 `knife_cut`。
- 同一颗产品的两块同时展示：V5；选择 `early_cohesive_opening` 或 `two_halves_display`。

一个静态画面只选择一个主状态和一个终点。动态镜头可沿合法状态链变化，但不能把多个终点同时塞入同一帧。

## 图片主体替换主流程

1. 查看原图实际像素，识别原主体食品、人物动作、手指接触、遮挡、透视、尺度证据、破损状态与最终静态终点。
2. 明确声明：原图是`编辑目标`；每张大福资产分别只承担颜色、表面、断边或构图参考，绝不整图继承。
3. 建立两张清单：
   - `replace_target`：只替换哪一个主体食品及其被遮挡部分、接触阴影和必要反射；
   - `locked_scene`：人物身份、脸、表情、身体、手、手指、指甲、服装、姿势、背景、机位、裁切、景深、光线、色调、原有物品和产品数量。
4. 按原动作选择最接近的 V1–V5 状态。不要为了展示产品而改变原图叙事；原图若是人物咬食，就用 V2，不要改成手托 V1。
5. 以同景深手指、掌心或嘴宽重建约 7 厘米、最大不超过 7.5 厘米的大福；视觉体量接近一颗标准网球。原食品的屏幕投影、外轮廓和像素占位不是锁定项：原食品较小时必须扩大到正确大福尺度，原食品较大时必须缩小。允许同步重建产品直接接触边缘、必要遮挡和接触阴影，但不得借机重画人物身份、脸、场景或机位。
6. 所有实际送入图像工具的 Prompt 使用中文并保存完整原文。编辑前列出唯一替换目标、锁定项、参考图角色和禁止漂移项。
7. 生成结果先进入 `candidates/` 或 `diagnostic-failures/`。查看原始像素后先执行尺寸门，再完成全部适用产品门槛与场景锁定项；即使已发现其他足以判退的错误，也不得省略尺寸结论或提前结束 QA。
8. 主体替换后若人物、手、机位、背景或光线漂移，直接拒绝；若产品有两个以上核心错误，整张从原始干净图片重做，不回喂失败图。
9. 只有一个局部主问题且其余产品和场景核心内容正确时，才允许一次单变量修改；仍失败或产生回归，停止该分支。

## 参考资产

- `assets/shell-color-surface-clean.png`：完整皮面颜色、明度、平滑粉感主参考；不继承盘子、尺寸、构图和光线。
- `assets/shell-color-surface-v1.png`：仅辅助核对完整冰皮的颜色与材质体系；不继承可见微纹。
- `assets/shell-color-surface-v3.png`：用户在 2026-08-15 指定替换到 03 位的内部测试图。只可研究双手拉开时内馅的大块软褶、黏稠附着和受力方向；禁止把它当作冰皮颜色、完整皮面、产品轮廓、静态终点或正式批准母版。其过曝黄光、黄色皮层、手套、背景包装、长距离拉伸和体积增长均禁止继承；若这些缺陷会污染生成，完全不附图。
- `assets/torn-edge-only.png`：仅参考圆钝、柔软、厚薄略变、向内弯曲贴馅的断边；不参考内馅、木桌、比例和光线。
- `assets/visual-mother-v4.png`、`visual-mother-v5.png`：仅参考对应状态的自然视觉分量、双手构图、暖调生活感与食欲感；不继承微纹、错误断边、错误内馅或尺寸。
- `assets/user-stretch-sequence-01.png` 至 `04.png`：用户提供的连续动作参考，只用于双手受力方向、渐进分离和软性形变时序；其中黄色外皮、超长条状拉伸、手套、托盘、背景小产品和体积增长全部禁止继承。静态 V3 默认最多附 01–02，03–04 只用于视频时序分析。
- `assets/v3-user-reference-01-thumb-pressure.png`：V3 `pressed` 阶段参考，只借鉴双拇指对称施压、中央宽软凹陷和初始受力关系；不继承手套、金属架、背景产品、尺寸、光线或已露馅结果。
- `assets/v3-user-reference-03-opening-seed.png`：V3 最早期 `opening_window` 的极小开口种子参考；不继承封闭规则孔洞、扁平大号椭圆、手和指甲、托盘、背景或尺寸。
- `assets/v3-user-reference-02-opening-window.png`：V3 已建立的较大 `opening_window` 参考；不得用于 `pre_break`，不得继承菱形封闭孔、扁平大号椭圆、手和指甲、托盘、背景或尺寸。
- `assets/filling-neutral-light-reference.png`：用户指定的中性光线内馅参考，只校准中性曝光下的明暗、色温与宽大连续软褶；不继承孔洞/空腔、开口几何、手、产品尺寸、背景或偏浅最终颜色，最终内馅仍服从暖金黄 90% 连续果泥加 10% 稀疏软纤维规范。
- `assets/approved-opened-display-texture-v5.png`：用户在 2026-08-16 明确批准的掰开展示质感案例。V5 `early_cohesive_opening`、`two_halves_display` 或 V4 `hand_torn` 需要质感校准时，只参考暖米白粉感冰皮、暖金黄浓稠满馅，以及柔软圆钝、厚薄自然、向内贴馅的断裂边缘；不把单颗落桌开口构图当作双手两半母版，不继承木桌、麻布、榴莲、茶具、背景、产品尺寸、开口轮廓或底部灰透薄皮。

静态 V3 只附与当前唯一终点匹配的一张形态参考：`pressed` 用 01，极小 `opening_window` 用 03，较大 `opening_window` 用 02；不得把 01–03 全部同时附上。01–03 均不构成合格 `pre_break` 参考。

若参考图的缺陷会强烈污染目标属性，不附该图，只使用文字规范。

## 交付纪律

- 实际像素中任何适用硬门槛为 `FAIL`，该图不得称为合格成片。
- 尺寸是独立先决门槛：报告必须记录同景深比例锚点、可见宽度比及 `PASS/FAIL`。不得用 Prompt 中出现“7 厘米”代替成图证据；缺少可靠比例证据时按 `FAIL`，不得猜测通过。
- 主体替换必须同时通过：场景锁定、动作匹配、自然尺度、冰皮、断边、内馅、数量和物理连续性。
- 五形态整套只有五张分别通过且并排属于同一产品和摄影体系时才算完成。
- 保存完整中文生成/编辑 Prompt、参考图角色、逐项 QA 与停止原因。
