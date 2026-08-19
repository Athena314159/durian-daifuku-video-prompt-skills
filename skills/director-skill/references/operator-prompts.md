# 操作指令

## 自然语言触发与默认路由

把“帮我对齐一下图文”“检查图文对齐”“同步图片和文字”“图片和 Prompt 对齐”“首帧和 Prompt 对齐”“检查 Word 图文是否对应”“S 编号和图片对不上”“Word 里的图片和文字错位了”“重新导出对齐后的 Word”及同义表达，路由到 `text_image_alignment`。当前对话已有唯一项目时立即使用该项目；没有项目或存在多个候选时，只询问项目目录或项目 ID。

按用户动词决定范围：

- “检查、看看、有没有对齐、不要修改”：只读比较并报告，不运行会改写派生文件的同步命令。
- “对齐、同步、帮我处理”：不改 canonical Prompt、不重新生图；从唯一事实源派生 TXT，检查现有 DOCX并生成 alignment manifest。
- “对齐并导出、重新生成 Word、align-and-export”：派生 TXT、重新导出 DOCX、运行 `align_exports.py --require-docx`，再用 Documents Skill 逐页检查。
- “修复错位、只修错误镜头”：读取 blocked S 编号，只修改对应镜头事实与派生交付；其他 aligned 镜头保持锁定。
- 用户明确指定 S 编号时只处理指定范围。

未明确要求重新生图时禁止生图；未明确要求修改 Prompt 时禁止改 canonical Prompt；不得因为 DOCX 需要整体重建而重生已批准图片。

## 图文同步与最终交付

```text
使用 $director-skill 继续项目 <项目绝对路径>，执行 align-and-export。
以 prompts/S*.md 的 text 代码块为唯一 Prompt 事实源，按 S 编号重新派生总 TXT 和逐镜 TXT，核对批准首帧、人物/换脸资产、产品资产、口播、Prompt 和 DOCX；运行 align_exports.py --require-docx，并使用 Documents Skill 渲染 DOCX 逐页检查。任一项不一致时只修对应 S 编号，不得从聊天摘要或旧 Word 重新扩写。
```

## 只检查，不修改内容

```text
使用 $director-skill，只检查项目 <项目绝对路径> 的图文对齐。读取 workflow_state.json 和现有交付，运行 align_exports.py --require-docx；不要改写 canonical Prompt、首帧或 Word，只报告 alignment_manifest.json 中的阻断项。
```

## 从阻断镜头继续

```text
使用 $director-skill 继续项目 <项目绝对路径>。先读取 planning/workflow_state.json 和 review/alignment_manifest.json，只处理 status=blocked 的 S 编号；通过后重新派生对应 TXT 和 DOCX，不重写其他 aligned 镜头。
```

## 低 Token 增量候选审核

```text
只扫描 <项目根目录> 下 planning/skill_update_candidates.json 中 status=new 的条目，不读取完整对话、视频、图片或 Word。运行 review_skill_candidates.py，去重并生成审核提案；不要修改任何 Skill。
```

## 用户批准后的 Skill 更新

```text
使用 $skill-creator 更新 $director-skill。只处理指定项目 skill_update_candidates.json 中 status=approved 且 user_approved=true 的条目；不读取完整对话，不写入项目专属人物、口播、镜头、临时比例或客户偏好。优先更新 reference 或 script，运行 quick_validate.py 和相关测试，成功后把候选标记为 promoted。不要更新 agents/openai.yaml。
```
