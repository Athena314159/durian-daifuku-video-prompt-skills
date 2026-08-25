# 操作指令

## 自动双开图文任务

只有原视频、尚无新版口播时：

```text
使用 $jimeng-video-remix-director，图文 Agent 帮我先跑一下这个视频。
```

总控必须先运行 `resolve_launch_contract.py`，得到 `execution_tier=source_intake` 和默认 `product_mode=preserve_source_product`。文 Agent 提取原片口播后在自己的任务贴出可编辑正文；总控不等待图 Agent，也在当前任务贴出同一正文。不得只报 handoff 路径，不得索要未被用户要求的目标产品参考，不得把等用户改稿写成 blocked。

只有用户明确要求完整交付、已锁定新版口播并明确产品模式时，才使用：

```text
使用 $jimeng-video-remix-director 帮我跑这个视频流程，自动开一个图任务和一个文任务。主题叫“麦乐森脆丝棒”，人物换成女性1，产品换成黄油脆丝棒。两个分支按锁定 shot map 并行完成全部镜头，不等待一镜样例；由当前总控合并并只交最终 DOCX。
```

总控必须真正创建两个侧边栏任务，并命名为当天的：

```text
M.DD｜麦乐森脆丝棒｜图Agent
M.DD｜麦乐森脆丝棒｜文Agent
```

不得只回复“已分成图线和文线”却没有创建任务；不得创建同名重复任务。总控必须执行 `list_threads → create_thread（缺失角色各一次）→ 必要时 set_thread_title → 双目标 wait_threads(timeoutMs=0)`，并在两个首条指令中写入 `branch_role`、`may_create_threads=false` 与解析后的阶段合同；只有 `full_delivery` 才写 locked shot-map/hash。

## 图文同步与最终交付

```text
使用 $jimeng-video-remix-director 继续项目 <项目绝对路径>，执行 align-and-export。
以当前 compile_id 下 prompts/S*.md 的 text 代码块为唯一 Prompt 事实源，先核对 canonical input hashes 与历史 input snapshot，再核对批准首帧、人物/换脸资产、产品资产、逐 SRC/ADD 口播、Prompt 和 DOCX；运行只读的 align_exports.py --require-docx，并使用 Documents Skill 渲染 DOCX 逐页检查。任一项不一致时只修对应 S/SRC/ADD，不得从聊天摘要或旧 Word 重新扩写，也不得让 aligner 回写 pack。
```

## 只检查，不修改内容

```text
使用 $jimeng-video-remix-director，只检查项目 <项目绝对路径> 的图文对齐。读取 workflow_state.json 和现有交付，运行 align_exports.py --require-docx；不要改写 canonical Prompt、首帧或 Word，只报告 alignment_manifest.json 中的阻断项。
```

## 从阻断镜头继续

```text
使用 $jimeng-video-remix-director 继续项目 <项目绝对路径>。先读取 planning/workflow_state.json 和 review/alignment_manifest.json，只处理 status=blocked 的 S 编号；通过后重新派生对应 TXT 和 DOCX，不重写其他 aligned 镜头。
```

## 低 Token 增量候选审核

```text
只扫描 <项目根目录> 下 planning/skill_update_candidates.json 中 status=new 的条目，不读取完整对话、视频、图片或 Word。运行 review_skill_candidates.py，去重并生成审核提案；不要修改任何 Skill。
```

## 用户批准后的 Skill 更新

```text
使用 $skill-creator 更新 $jimeng-video-remix-director。只处理指定项目 skill_update_candidates.json 中 status=approved 且 user_approved=true 的条目；不读取完整对话，不写入项目专属人物、口播、镜头、临时比例或客户偏好。优先更新 reference 或 script，运行 quick_validate.py 和相关测试，成功后把候选标记为 promoted。不要更新 agents/openai.yaml。
```
