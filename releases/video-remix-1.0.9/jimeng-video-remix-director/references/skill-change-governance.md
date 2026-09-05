# Skill 变更与发布门禁

## 目录

1. 目标
2. 唯一允许的变更路径
3. 规则优先级与替换原则
4. 版本与项目锁
5. 发布门
6. 灰度与回滚

## 1. 目标

防止通过不断追加规则，让旧能力在没有被发现的情况下退化。任何跨项目修复都先成为候选版本；live Skill 只接收通过发布门的完整版本，禁止在 live 目录临时补一句、补一个 if 或直接改 Prompt 模板。

## 2. 唯一允许的变更路径

1. 在项目 `planning/skill_update_candidates.json` 记录可观察问题、证据、影响面、风险、拟替换的旧规则和新增回归案例。
2. 运行 `review_skill_candidates.py`；项目专属偏好、具体人物、具体口播、S 编号和临时比例不得升级为全局规则。
3. 从当前稳定发布复制一个候选目录。禁止直接编辑 live Skill。
4. 修改候选时优先“替换/收敛旧规则”，不是继续追加同义规则。每项变更必须声明 `replaces`、`interaction_surfaces`、`regression_case_ids` 和 `rollback_trigger`。
5. 更新两个 Skill 的 `references/skill-release.json`，两个文件的 `bundle_release_id` 与 `prompt_authoring_contract` 必须一致。
6. 运行 `skill_release_gate.py`。旧黄金案例、新案例、两个 Skill 的单元测试、静态合同、行数预算和版本关系必须全部通过。
7. 将候选保存为不可变发布快照，再安装到 live。安装后对 live 再跑同一发布门；失败立即恢复上一快照。

## 3. 规则优先级与替换原则

优先级从高到低固定为：

`用户本次明确要求 → 原片可见/可听事实 → 项目锁定口播与镜头 → 已批准人物/产品/包装事实 → narrative-six-layer-v1 → 通用质量保护 → 可选风格建议`

低优先级规则不得覆盖高优先级事实。新规则与旧规则作用于同一属性时必须替换旧规则或明确适用条件，禁止让两条冲突规则同时存在。Prompt 作者层、证据账本、QA、交付排版保持分层；内部审计字段和限制词不得回流成 Prompt 正文。

## 4. 版本与项目锁

新项目在 `project.json.skill_release_lock` 保存：

- `bundle_release_id`
- `prompt_authoring_contract`
- `auto_upgrade=false`

项目从创建到最终 Word 都沿用该锁。live Skill 更新不自动改写已有项目。需要升级旧项目时，先复制项目、显式改锁、重新编译并跑全量回归；原项目保留可回滚。

## 5. 发布门

发布必须同时满足：

- 两个 release manifest 同版本、同 Prompt 作者合同；
- `SKILL.md` 不超过 500 行；新增细节进入单层 reference，防止主 Skill 无限膨胀；
- `narrative-six-layer-v1` 的七段顺序、情绪因果、审计字段隔离、负面比例、长度归属和 DOCX 唯一交付全部保留；
- 旧黄金案例全部通过，新失败必须先新增能失败的回归案例，再修代码直至通过；
- 两个 Skill 的全部发布测试返回 0；
- 候选版本严格高于上一稳定版，`supersedes` 指向上一版；
- 发布报告为 `valid`，不得凭人工口头“看过了”安装。

## 6. 灰度与回滚

- 首个真实项目使用新发布时设为灰度项目；只比较新旧 Prompt 作者层，不重复 ASR、提帧或生图。
- 内容复核至少抽查：开头钩子、普通人物口播、纯产品展示、吃食、人物掰断、纯手掰断、包装和结尾收束。
- 任何黄金案例回退、限制语暴增、叙事段缺失、旧产品污染、Word 图文错位或 live 二次校验失败，立即触发回滚。
- 回滚只切回上一完整发布快照，不在失败 live 版本上继续打补丁。
