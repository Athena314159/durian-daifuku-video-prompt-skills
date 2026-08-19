# 项目状态、图文对齐与增量学习

## 1. 并发边界

- 共享 Skill、脚本、正式人物库和正式产品库按只读处理。
- 每个项目使用稳定且唯一的目录；禁止把 `current`、`latest` 或共享根目录作为可写项目。
- 每个对话默认只写一个项目。多个对话操作同一项目时，按互不重叠的 S 编号分工；重拆镜、重编号、角色锁、总 TXT、DOCX 和最终 manifest 只允许一个写入者。
- `scripts/workflow_state.py` 使用项目级文件锁更新状态；不得把当前阶段保存在聊天记忆或 Skill 文件夹。

## 2. `planning/workflow_state.json`

保存 `project_id`、`current_stage`、`status`、`canonical_prompt_source`、`completed_stages`、`blocked_by`、`next_allowed_actions`、规则版本和更新时间。阶段顺序：

`intake → transcript_handoff → revised_script_lock → role_lock → asset_inventory → storyboard_approval → first_frame_approval → prompt_compile → text_image_alignment → docx_render_qa → complete`

恢复任务时先读取该文件，只从 `current_stage` 继续。缺输入时写结构化阻断项，不从头重做。

## 3. 唯一 Prompt 事实源

`prompts/<S编号>.md` 中的 `text` 代码块是唯一 Prompt 事实源。以下交付只能由它派生，禁止分别编辑：

- `exports/完整逐分镜Prompt.txt`
- `exports/shots/<S编号>_<标题>.txt`
- `prompts/generation_pack.json` 中的 Prompt 哈希
- `exports/<项目名>.docx` 中的“即梦可复制 Prompt”

运行 `scripts/align_exports.py` 后，`review/alignment_manifest.json` 按镜记录 canonical Prompt SHA-256、逐镜 TXT、总 TXT、DOCX 精确文本、批准帧 SHA-256、DOCX 实际内嵌媒体哈希、人物和产品绑定。任一必需检查失败时状态为 `blocked`，只修对应 S 编号。

## 4. `planning/skill_update_candidates.json`

每条候选至少包含：

```json
{
  "candidate_id": "RULE-YYYYMMDD-001",
  "category": "alignment",
  "observed_problem": "可观察问题",
  "proposed_rule": "可跨项目执行的规则",
  "scope": "cross_project",
  "evidence": ["review/evidence.json"],
  "target_skill": "director-skill",
  "target_resource": "references/workflow.md",
  "status": "new",
  "user_approved": false
}
```

状态只使用 `new`、`reviewed`、`approved`、`rejected`、`promoted`。日常扫描只读取 `new` 候选，不重读完整对话、视频、图片和 Word。`review_skill_candidates.py` 只做结构化去重、范围与证据检查，不修改 Skill。

只有 `scope=cross_project`、`status=approved`、`user_approved=true` 且证据可访问的候选，才允许使用 `$skill-creator` 写回 Skill。项目人物、具体口播、S 编号、临时比例、单客户偏好和未经批准的产品事实永不提升为全局规则。

## 5. 推荐运行顺序

```bash
python3 scripts/workflow_state.py --project-dir <project> init
python3 scripts/pipeline.py lint --project-dir <project>
python3 scripts/pipeline.py compile --project-dir <project>
python3 scripts/export_jimeng_docx.py --project-dir <project> --out <project>/exports/<name>.docx
python3 scripts/align_exports.py --project-dir <project> --docx <project>/exports/<name>.docx --require-docx
python3 scripts/review_skill_candidates.py --project-dir <project>
```

`alignment_manifest.summary.status=aligned` 后使用 Documents Skill 渲染并逐页检查 DOCX。视觉检查通过后，才把 `workflow_state.current_stage` 设为 `complete`。

## 6. 自然语言进入对齐节点

用户表达“帮我对齐一下图文”或同义意图时进入 `text_image_alignment`。普通“对齐”默认允许刷新派生 TXT 和 alignment manifest，但不改 canonical Prompt、不重新生图；“只检查”保持只读；只有“对齐并导出/重新生成 Word”才重建 DOCX。完整短语与路由见 [operator-prompts.md](operator-prompts.md)。
