# V2 Canonical合同

## 单一事实源

`canonical_project.json` 必须包含：项目与源视频、目标时长、角色、完整口播line、源镜头、生成镜头、源帧、产品与包装、吃播候选、掰开候选和批准资产。任何派生文件不得反向修改Canonical事实。

此外，必须包含 `semantic_role_performance_gate_file`。该文件按 `extract-video-prompt/references/semantic-role-performance-gate.md` 保存角色镜像测试、逐句真值表、源动作证据、空间不变量、吃食/口播占用、六层叙事和语音证据。每个生成镜头还必须声明有序`source_shot_ids`、`shot_mode`、源状态和允许包装层级。启用`performance_contract_version`后，人物镜头必须补齐`source_performance_evidence`：`gaze_path`、`facial_micro_reactions`、`shoulder_weight_shift`、`hand_roles`、`voice_observation`、`emotion_landing`和`source_anchor_terms`；无人物镜头必须补齐`finger_force`、`weight_transfer`、`packaging_friction`、`focus_path`和`product_change`，并且不含脸部表演证据。V2 编译器将其作为硬输入校验；没有通过语义门禁时不得编译正式任务。

## 编译输出

- `final_generation_manifest.json`：唯一生成镜头顺序。
- `script_shot_map.json`：line ID到镜头、说话人和声源的唯一映射。
- `image_task_manifest.json`：每个图像任务的源帧、锁定区、替换区、尺度、状态和参考资产。
- `prompt_task_manifest.json`：事实动作和叙事增强合同。
- `rule_receipt.json`：规则版本、输入哈希和硬门状态。
- `semantic_role_performance_gate.json`：编译时锁定的语义门禁副本与哈希来源。

## 角色

人物数量从源片自动识别。单人物不补第二人物；多人使用稳定ID。画外角色必须有源片证据或用户明确指定。每句台词只能绑定一个角色和一种屏内状态。

## 镜头

只按硬切分源镜头；无硬切连续运镜合并。图片数量不等于镜头数量。正式`generation_shots`只能使用源片锁定的S编号并按源顺序排列。补入事件可以有内部事件ID，但必须绑定既有源帧、哈希和既有S镜头，不能伪造新分镜或原片时码。

## 20秒事件规则

目标成片达到20秒时：吃食目标为 `max(原片已有有效事件数, 3 + floor((目标时长-20)/10))`；低于20秒不自动补入。原片和批准示范已有的有效入口全部保留，只补不足数量。掰开目标为 `max(原片已有有效过程数,2)`。补入事件使用原片人物/手部候选帧并绑定既有S镜头。掰开过程从完整→按压→整体拉伸→小撕口→露馅→拉丝→断开→回弹。

## 图像合同

每个任务包含不可变源帧哈希、锁定项、局部替换项、bbox、尺度锚点、产品实例ID、状态、包装层级和参考角色。人物身份、场景、机位、光线、构图默认锁定。包装文字优先确定性投影，不让模型自由重画。

## Prompt合同

第一阶段只写事实动作、声源、手口占用、产品物理和运镜。第二阶段按镜头类型加入对应的意图、视线、眉眼、嘴角、双颊、呼吸、身体重心、手指受力、感官反馈和情绪落点，输出连续故事脚本。人物镜头写目光、眉峰、嘴角、肩线和双手职责，并且每一处反应必须回扣`source_performance_evidence`中的观察；纯手部只写指腹受力、重量、包装摩擦、焦点和产品变化；微距只写产品状态、手部承托、景深和材料反馈。复制Prompt不输出内部因果字段名“触发”。正式台词只由script map注入一次；叙事段使用line ID或概述。不得用“按原片节奏”“自然口语”“自然完成”“活人感”作为动作证据或长度填充。

## 自动修复

口播漏句/重复/逆序、镜头合并后的映射、事件数量、软性叙事不足和水词先自动修复，最多两轮。只有说话人、屏内/画外或身份参考无法从事实推断时才询问一次。
