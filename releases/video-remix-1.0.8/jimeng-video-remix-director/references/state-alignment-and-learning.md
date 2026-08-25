# 项目状态、图文对齐与增量学习

## 1. 并发边界

- 共享 Skill、脚本、正式人物库和正式产品库按只读处理。
- 每个项目使用稳定且唯一的目录；禁止把 `current`、`latest` 或共享根目录作为可写项目。
- 每个对话默认只写一个项目。多个对话操作同一项目时，按互不重叠的 S 编号分工；重拆镜、重编号、角色锁、总 TXT、DOCX 和最终 manifest 只允许一个写入者。
- `scripts/workflow_state.py` 使用项目级文件锁更新状态；不得把当前阶段保存在聊天记忆或 Skill 文件夹。

## 2. `planning/workflow_state.json`

保存 `project_id`、`current_stage`、`status`、`source_ready`、`pending_inputs`、`canonical_prompt_source`、`completed_stages`、`blocked_by`、`next_allowed_actions`、规则版本和更新时间。阶段顺序：

`intake → transcript_handoff → revised_script_lock → role_lock → asset_inventory → storyboard_approval → first_frame_approval → prompt_compile → text_image_alignment → docx_render_qa → complete`

恢复任务时先读取该文件，只从 `current_stage` 继续。状态只使用：

- `in_progress`：本阶段正在执行；
- `source_ready`：原片口播/视觉 intake 已产出且可直接交给用户；即使 `pending_inputs` 仍列出下一阶段的 `revised_script` 或 `target_product_reference`，也保持该完成状态；
- `awaiting_user_input`：本阶段尚无可用成果，但等待普通用户输入即可继续；
- `blocked`：真实技术错误、不可满足的事实冲突或结构验证失败；
- `complete`：最终交付已完成。

缺新版口播或目标产品参考时写入 `pending_inputs`，不得写入 `blocked_by`。用户未明确换品时使用 `preserve_source_product`，不得产生 `target_product_reference` 待输入。`transcript_handoff` 完成后先设 `source_ready=true`，状态保持 `source_ready`；是否仍等用户改稿由 `pending_inputs` 单独表达。总控和文分支都必须先贴出可编辑正文，不能把“等待修改”理解为不展示原稿或未完成。

分支是用户可见任务。用户若在 source-intake 文分支补充或确认口播，分支更新自己的 handoff，并用 `send_message_to_thread` 定点通知既有总控；若工具暂不可用，总控应通过后续 `wait_threads` 快照接收。不得要求用户在多个任务重复粘贴同一输入。

## 3. 唯一 Prompt 事实源

`prompts/<S编号>.md` 中的 `text` 代码块是唯一 Prompt 事实源。以下交付只能由它派生，禁止分别编辑：

- `exports/完整逐分镜Prompt.txt`
- `exports/shots/<S编号>_<标题>.txt`
- `prompts/generation_pack.json` 中的 Prompt 哈希
- `exports/<项目名>.docx` 中的“即梦可复制 Prompt”

运行只读的 `scripts/align_exports.py` 后，`review/alignment_manifest.json` 记录 compile ID/canonical input hashes、canonical Prompt SHA-256、DOCX 精确可编辑文本，并按正文 `SRC/ADD` 卡记录批准帧 SHA-256、OOXML relationship 实际图片哈希、人物和产品绑定。封面媒体不参与正文卡判断；任一必需检查失败时状态为 `blocked`，只修对应 S/SRC/ADD。aligner 不得回写 generation pack、Prompt、TXT、图片或 Word。

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
  "target_skill": "jimeng-video-remix-director",
  "target_resource": "references/workflow.md",
  "risk_level": "medium",
  "interaction_surfaces": ["prompt_compile", "docx_alignment"],
  "regression_case_ids": ["GOLDEN-DOCX-001"],
  "replaces": [],
  "rollback_trigger": "Prompt 唯一事实源或 Word 对齐回退",
  "status": "new",
  "user_approved": false
}
```

状态只使用 `new`、`reviewed`、`approved`、`rejected`、`promoted`。日常扫描只读取 `new` 候选，不重读完整对话、视频、图片和 Word。`review_skill_candidates.py` 只做结构化去重、范围与证据检查，不修改 Skill。

只有 `scope=cross_project`、`status=approved`、`user_approved=true`、证据可访问、影响面/回归案例/替换关系/回滚触发均完整的候选，才允许进入候选 Skill 版本。随后仍必须执行 `skill-change-governance.md` 与 `skill_release_gate.py`；候选审核通过不等于允许直接写 live。项目人物、具体口播、S 编号、临时比例、单客户偏好和未经批准的产品事实永不提升为全局规则。

## 5. 推荐运行顺序

```bash
python3 scripts/workflow_state.py --project-dir <project> init
python3 scripts/pipeline.py lint --project-dir <project>
python3 scripts/pipeline.py compile --project-dir <project>
python3 scripts/export_jimeng_docx.py --project-dir <project> --out <user-output>/<name>.docx --manifest-out <project>/review/<name>.manifest.json
python3 scripts/align_exports.py --project-dir <project> --docx <user-output>/<name>.docx --require-docx
python3 scripts/review_skill_candidates.py --project-dir <project>
```

`alignment_manifest.summary.status=aligned` 后使用 Documents Skill 渲染并逐页检查 DOCX。视觉检查通过，并确认用户输出目录只有这一份 DOCX 后，才把 `workflow_state.current_stage` 设为 `complete`。TXT、JSON、manifest、对齐报告和分支 handoff 均留在项目内部。
