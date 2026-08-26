# 榴莲大福 v2 总控集成合同

## 适用范围

目标产品为榴莲冰皮大福时使用 `durian-daifuku-v2`。`durian-daifuku-v1` 只保留旧项目回放，不得用于新项目或新编译。

## 初始化

运行：

```bash
python3 <skill-dir>/scripts/init_project.py \
  --name <project-name> \
  --output <projects-directory> \
  --product-mode replace_product \
  --product-profile durian-daifuku-v2 \
  --style-profile ugc-food-review-v1
```

初始化必须同时完成：

1. 把 v2 写入 `library/product_bible.json`。
2. 把 v2 规则与图片条目写入非空 `library/knowledge_index.json`。
3. 把批准参考资产复制到项目 `source/references/durian-daifuku-v2/`，保存 SHA-256、唯一角色、允许继承和禁止继承。
4. 把项目 release lock 固定到当前 bundle，`auto_upgrade=false`。

## 逐镜结构化合同

每镜在 `product_state` 中保存：

```json
{
  "profile": "durian-daifuku-v2",
  "state": "opening_window_seed",
  "scale_lock": {
    "mode": "physical_consistency",
    "source_scale_role": "pose_only_incompatible_scale",
    "anchor": {
      "type": "index_finger_mid",
      "expected_ratio": [4.0, 4.4],
      "evidence": "产品与接触食指同景深，完整可重建宽度按食指中段复核"
    }
  },
  "surface_lock": {
    "rice_flour_haze": true,
    "visible_in_oblique_light": true,
    "individually_resolvable_particles": false
  },
  "filling_lock": {
    "continuous_puree_ratio": 0.9,
    "countable_lumps": false,
    "holes_or_honeycomb": false,
    "stringing": false
  },
  "endpoint_lock": {
    "terminal_state": "opening_window_seed",
    "single_endpoint": true,
    "max_visible_filling_area_ratio": 0.05,
    "piece_air_gap_cm": 0
  },
  "reference_roles": [
    {
      "asset_id": "DF2-SURFACE-01",
      "role": "shell_color_and_smooth_base_only",
      "allowed_inheritance": ["暖米白固有色", "平滑完整皮面基底"],
      "forbidden_inheritance": ["盘子", "产品占盘比例", "完整构图", "产品尺寸", "完全无粉的面团观感"]
    },
    {
      "asset_id": "DF2-OPENING-SEED-01",
      "role": "opening_topology_only",
      "allowed_inheritance": ["双手初始受力位置", "首次微露馅的时间状态"],
      "forbidden_inheritance": ["产品大小", "扁平轮廓", "手和指甲身份", "背景产品", "光线", "规则孔形"]
    }
  ]
}
```

`reference_roles` 中每个资产必须在 `asset_links.product_references` 出现一次，反向也一样。图片只能用于其 `allowed_states`；大开口、两半或完整断面资产不能进入 `opening_window_seed`。

## 尺度

- 手持/撕拉优先使用同景深食指中段，完整或可重建宽度为 4.0–4.4 指宽；若使用掌宽则为 0.88–0.96。结果至少两个同景深手部锚点同时通过，空白/纯场景母版不能授权手部互动镜头。
- 人物咬后另用嘴宽复核。
- 盘装镜必须使用已知盘子内径或批准的同场景尺度母资产；未知盘面外观不能证明 7 厘米。

### 生图前像素预检硬门

文字尺度合同只描述物理目标，不能直接授权一次生成。每个需要生图或改图的镜头先在精确 `source_first_frame` 上测量同景深锚点像素宽度，再运行：

```bash
python3 <skill-dir>/scripts/prepare_daifuku_pixel_preflight.py \
  --project-dir <project-directory> \
  --shot-id <shot-id> \
  --anchor-type index_finger_mid \
  --anchor-bbox <x> <y> <measured-width-px> <measured-height-px> \
  --selected-ratio 3.75 \
  --target-center <x> <y> \
  --evidence "同景深食指中段的可见横向宽度"
```

脚本以本地像素运算生成 `review/scale-guides/<shot>-daifuku-scale-guide.png` 与同名 JSON，不消耗任何生图调用。逐镜 `scale_lock.pixel_plan` 必须包含并通过：原帧路径与 SHA-256、原帧尺寸、画面内可复算的锚点标注框、允许比例与选定比例、目标宽高、宽度容差、`bbox_xywh`、产品状态/锚点/产品合同/release 绑定、几何引导图路径/哈希/角色及 manifest 路径。盘装镜改用 `known_container_dimension` 或 `approved_scene_scale_master`，不得拿未知盘面外观猜大小；但 `approved_scene_scale_master` 绝不能单独授权手持、按压、咬食或撕拉。手部互动成品另需哈希绑定的 `durian-daifuku-scale-relationship-qa-v1.0`，至少两个物理锚点同时通过，否则停止批准。

调用生图工具时必须同时提供精确原帧和该几何引导图；Prompt 明说引导图仅约束目标椭圆的位置与尺寸，不继承青色轮廓、十字、标签、文字或其他覆盖层。任一像素计划字段缺失、算术不一致、目标框越界或哈希变化时触发 `DAIFUKU_PIXEL_PREFLIGHT_*`/`DAIFUKU_SCALE_GUIDE_*` 并在调用前阻断。一个有效计划只允许一次初始生成；尺寸失败后先改变可审计计划或选择确定性局部编辑，禁止原样盲重跑。

### 原子生图授权与联合结果回执

运行 `scripts/image_generation_gate.py authorize` 取得当前 release 的生图授权。回执必须绑定精确原始首帧、几何导引图、完整中文 Prompt、产品参考、产品 bible 及全部 SHA-256；换头镜再绑定已授权人物参考。若人物和产品都要替换，`requested_edits` 必须恰好为 `identity + product` 且 `atomic_identity_product=true`，不得拆成身份清洁帧和产品补改两轮。

结果返回后运行 `record-result`，以同一候选图联合记录身份、产品、尺寸、粉雾表皮、连续果泥、开口终点、构图和原图来源，并用输出产品实测 `bbox_xywh` 对照预检宽度容差。任一项失败时状态固定为 `rejected_diagnostic`、`retry_instruction=return_to_exact_original_source`、`partial_candidate_reusable=false`；禁止写入批准首帧或资产复用计划。只有同一候选全部通过且输出哈希匹配时才允许继续用户确认和交付。
- 原食品默认 `pose_only_incompatible_scale`。只有可证明同规格时才使用 `compatible_scale_anchor`。
- 首帧通过后仍检查产品移动、接近人物、建立撕口和动作终点；尺寸随动作跳变即拒绝。

## 小开口终点

用户要求“正要掰开、冰皮拉伸、微微露馅”时使用 `opening_window_seed`，不是 `pre_break`、`early_cohesive_opening` 或 `two_halves_display`。终点保持：

- 开口主轴建议 4–8 毫米；
- 可见内馅面积不超过产品正面约 5%；
- 两侧主体没有空气间隙；
- 产品仍是一颗连续主体；
- 第一次看见内馅后动作立即停止。

不要在同镜的目的、动作节拍或原片复原文本中保留“掰成两半、展示断面、拉开满馅”等后续完成态。如果原片后续动作必须保留，拆成下一独立状态镜头。

## 表皮与内馅

完整皮面必须同时具有平滑基底和侧向柔光可见的极薄细糯米粉雾层。粉雾不可逐粒辨认；既不能成为粗粉点，也不能退化成完全无粉的光滑面团。

内馅至少 90% 为无清晰颗粒边界的连续浓稠果泥，少量软纤维自然融入。不得出现可数疙瘩、米粒、孔洞、蜂窝、黄色细丝或薄膜。`filling-neutral-light-reference.png` 含大开口与空腔污染，不进入 `opening_window_seed`；小开口镜用文字和当前小开口角色资产即可。

## 旧项目迁移

运行 `scripts/migrate_durian_daifuku_v2.py` 创建项目副本。它同时处理 v1 产品迁移和旧 release 的 v2 项目迁移，绝不覆盖原项目；原项目是唯一回滚源。活动副本必须删除旧 Prompt、旧交付/生图回执、旧尺寸导引、标准及非标准候选/批准目录、旧 QA、旧 continuity lock、v1 参考目录和 target 内部旧版归档，并解除全部批准首帧绑定；`source_intake_contract` 中当前产品/release 绑定改写为 v2/当前 release。清理后递归扫描非审计白名单中的旧 profile/release token，命中即以 `MIGRATED_ACTIVE_COPY_LEGACY_CONTAMINATION` 失败关闭。`split` 与 `stretched` 语义不唯一时必须人工重建逐镜状态；所有镜头都必须重建像素计划和生图授权，再重新 lint、compile 与验证交付回执。

若 `planning/user_directives.md` 以“依据 video-remix-x.y.z”明确声明当前执行基线，迁移器只把这一种语法重绑为当前 release，并在 cleanup receipt 记录路径；整条用户要求保持不变。其他旧版本号提及不自动改写，因为它可能表示禁用、历史对照或回滚事实，仍由递归污染扫描失败关闭并要求人工判定。

## 完整性、实例与库存

- 每镜必填 `integrity_lock` 与 `instance_lock`；多实例、摆盘、容器或包装镜另填 `arrangement_lock`。
- `integrity_lock` 保存当前状态、开口来源、露馅边界和五类禁形布尔值；`bitten` 另含单个局部内凹咬口、牙压证据和 15%–30% 缺失质量。
- 人物吃的 `bitten` 还必须保存 `opening_direction=toward_mouth_and_person`、`mouth_contacts_opening_side=true`、`camera_facing_opening=false` 与方向证据；破口正对镜头/观众或嘴接触完整背面触发 `DAIFUKU_BITE_ORIENTATION_INVALID`。
- `instance_lock` 保存原片数量、目标数量、变化事件、稳定实例 ID、独立形态变体、共同约7厘米尺寸等级和接触形变。原片两颗/三颗无事件时必须等数替换；同尺寸不允许实例克隆。
- `arrangement_lock` 保存 layout/container/inventory-stage、实例顺序、上一布局和拿取事件。拿起一颗后容器库存严格减一，余下相对拓扑保持；包装/独立袋须自然错位，禁止完美网格、等距和全平行。
- 所有结果提交 `topology_qa` 与 `instance_qa`；适用镜头再提交 `continuity_qa`。大坑假咬口、数量少替换、实例克隆、过度整齐或拿取后库存不减均整张拒收并返回精确原图。

## 运输纸箱与外皮像素门

- 精确原帧先登记 `source_package_inventory`，包装存在性、层级和数量不能由目标 Prompt 擅自隐藏或添加。
- 出现 `shipping_carton` 时先运行 `prepare_shipping_carton_capacity_plan.py`。纸箱尺寸可依据原片容器 bbox、同景深锚点、固定15×15×4.5厘米零售盒数量与布局调整；只有在明确场景硬尺寸上限内无任何可行布局时，返回 `SOURCE_CONTAINER_CAPACITY_CONFLICT` 并停止生成。
- 运输箱复合透视母版的正面/侧面使用 `project_package_master.py --master-source-quad ...` 分面校正，顶部内侧仅作结构参考。
- 成图后运行 `audit_daifuku_surface.py` 并把报告通过 `image_generation_gate.py record-result --surface-qa` 绑定；灰棕/黑褐石头色与砂石水泥颗粒不允许人工绕过。
- lint 同时交叉检查叙事职责、结构化终点、内馅物理和声源：`opening_window_seed` 不得保留两半/断面目的，`plated` 不得沿用互补两半，无独立画外音证据不得发明旁白，“爆浆”口播不得视觉液态化。
