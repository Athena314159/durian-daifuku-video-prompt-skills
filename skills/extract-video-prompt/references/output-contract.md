# 输出契约、角色锁与阻断码

## 1. 最终目录

使用 UTF-8 输出；总文件与逐镜文件的同一镜头必须逐字一致。

```text
<output-directory>/
├── 完整逐分镜Prompt.txt
├── 人物角色锁定表.txt
├── 内容语义审核报告.txt
├── planning/
│   └── role_lock.json
└── shots/
    ├── S001_标题摘要.txt
    ├── S002_标题摘要.txt
    └── ...
```

标题优先取本镜口播的核心内容；无口播时用一句简短动作摘要。文件名去除路径非法字符。

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
  "required_actions": [
    {
      "shot_id": "S005",
      "kind": "bite_chain",
      "source_time": "00:16.300–00:19.800",
      "evidence": "原片人物完成吃、咬或等价咬食动作"
    }
  ]
}
```

`visibility` 使用：

- `offscreen_all`：全程镜头后或镜外；不得生成该人物任何可见身体信息；
- `onscreen_all`：全程画面中；
- `partial`：按分镜另行列出可见范围。

当原片含吃或咬动作时，在 `required_actions` 写入 `bite_chain`，让自动检查确认 Prompt 至少包含接近口部、张口/咬合、撤回/离嘴、形成咬口/断面、咀嚼/吞咽。自动命中只表示关键词链存在，仍需人工逐帧核对动作顺序和物理结果。

## 4. 总 TXT 与逐镜 TXT

```text
==================================================
S001｜“对应口播核心内容”
==================================================
原片时间：00:00.000–00:05.200
独立生成时长：5.200秒
人物位置：男生始终在画面中；女生始终位于镜头后且不出镜
声音方式：女生镜外现场对白；男生屏内对白
产品形态：完整未破、手持展示
生成首帧：<路径、资产ID或待制作>

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
- 每镜通常 2–3 句台词，动作逻辑优先；
- 较长单句原则上约 50 个汉字；
- 完整编译的 Prompt 主体必须为3000–4000个非空白字符；默认压在3000–3300安全区，避免复制区标签、换行或平台计数差异造成超限；
- 由人物、手部或人物声音推进的镜头，六层表演及其可观察动作必须占 Prompt 主体非空白字符的 40%–50%；只统计绑定本镜时间段、触发和反应的专属内容，不统计身份锁、产品/摄影约束、通用负面词或跨镜复用模板；
- 导出时逐镜记录表演字符数、Prompt 主体字符数和表演占比；程序比例通过不能替代逐动作段六层内容审核；
- 字符数按去除全部空白后的 Prompt 主体计算，标题标注必须与程序实算一致；
- 不得以复制句子、同义反复、无关排除词或虚构不可观察细节凑长度；Prompt复制区只保留生成可执行内容，原片证据解释、审核过程、版本说明和统计方法全部放在复制区外；
- 产品形态只写中文，不写 `V1–V5`、`whole`、`bitten`、`person_eating` 等内部标签；
- 用户交付不使用 `PASS`、`FAIL`、`ERROR` 冒充内容审核结论；
- `【原片动作对应】` 要写动作事实和时间映射，不能只写“已覆盖”；
- `【内容审核记录】` 要写具体角色、台词、动作和待看像素，不能只写状态词。

## 5. 分镜确认卡

长 Prompt 前每镜提交短卡：

```text
S001｜标题
原片时间 / 生成时长：
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
仍需确认的冲突：
```

用户尚未发回新版口播时，可填写原片事实，但口播与台词视觉证据写 `等待用户发回新版口播`，不得自行补写。

## 6. 阻断码

- `MISSING_SOURCE_VIDEO`：没有原视频，无法复刻。
- `ORIGINAL_TRANSCRIPT_NOT_HANDED_OFF`：尚未提交原口播/原字幕。
- `WAITING_FOR_REVISED_SCRIPT`：等待用户发回新版口播，只能做原片先行分析。
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
- `PROMPT_TOO_SHORT`：完整编译主体少于3000个非空白字符。
- `PROMPT_TOO_LONG`：完整编译主体超过4000个非空白字符，或项目已锁定更小上限却仍超过该上限。
- `PROMPT_COPY_REGION_CONTAMINATED`：即梦复制区混入原片证据解释、审核过程、版本说明、统计方法或与本镜无关的跨镜知识。
- `PROMPT_CHAR_COUNT_MISMATCH`：标题标注的主体字符数与程序实算不一致。
- `PROMPT_PADDING_DETECTED`：出现明显复制、同义堆叠或与本镜无关的长度填充。
- `DOC_PROMPT_SOURCE_DIVERGENCE`：Word 没有逐镜使用已校验 TXT 正文，或图片、台词、字符数与事实源不一致。
- `DOC_RENDER_NOT_VERIFIED`：DOCX 未渲染并逐页检查版式、中文字体、图片和截断。
- `GENERATION_TIME_NOT_ZERO`：镜内时间未从 0.00 秒开始。
- `NO_TEXT_RULE_MISSING`：缺无字幕无水印规则。
- `PERFORMANCE_DETAIL_MISSING`：缺视线、五官、身体/手部、呼吸/停顿或声音细节。
- `PERFORMANCE_SHARE_OUT_OF_RANGE`：六层表演及其可观察动作占 Prompt 主体少于 40% 或超过 50%，或统计口径混入身份锁、产品/摄影约束和通用模板。
- `ACCENT_PLAN_MISSING`：有口播却没有具体可执行的口音与讲话方案。
- `ACCENT_PROPOSAL_NOT_DISCLOSED`：原声证据不足时提出了口音方案，却没有在对话中说明它属于创作提案。
- `GENERIC_SPEECH_PLACEHOLDER`：使用“沿用原片生活口语节奏”等泛化占位表达。
- `STRUCTURE_RESULT_MISREPRESENTED_AS_CONTENT_AUDIT`：把自动结构检查写成内容审核结论。
- `TXT_EXPORT_MISSING`：总 TXT、逐镜 TXT、角色锁或审核报告缺失。
