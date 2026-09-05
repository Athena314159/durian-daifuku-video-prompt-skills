# Seedream 5.0 Pro provider 合同

V2 Canonical 的图像层默认 provider 为 `seedream`，默认模型标识为
`seedream-5.0-pro`；Ark 请求模型 ID 为 `doubao-seedream-5-0-260128`（可用
`ark_model_id` 覆盖）。它只消费编译后的 `image_task_manifest.json`，不得从聊天文本绕过
Canonical 直接生成。

每个任务提交时应保留：

- `source_frame` 与 `source_frame_sha256`
- `locked_regions`、`replace_regions`、`bbox_xywh` 和 `scale_anchors`
- `product_state`、产品实例和包装状态
- `provider.type` 与 `provider.model`
- 编译目录中的 `rule_receipt.json`

Seedream 输出用于静态资产或视频首帧。视频生成（例如 Seedance）是独立 provider，不能
把 Seedream 的图像任务误当成视频任务。实际 endpoint、鉴权和可用模型 ID 由部署方的
Seedream/火山引擎账户配置提供，不能在技能包中硬编码密钥。官方 Ark 图片生成接口为
`POST https://ark.cn-beijing.volces.com/api/v3/images/generations`；执行器从环境变量
`ARK_API_KEY` 读取密钥。
