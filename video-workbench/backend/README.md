# 即梦视频工作台本地后端

这是电脑版工作台的本地编排服务，不是演示数据后台。它把网页操作写入与 `jimeng-video-remix-director` 共用的项目 JSON，调用已安装的导演脚本、`ffprobe`、`ffmpeg`，并可在项目明确开启后调用 `codex exec --json`。

服务只使用 Python 3.9 标准库，默认监听 `127.0.0.1`。项目、视频、知识库、任务、事件和审批均保存在本地；长任务脱离单次 HTTP 请求运行。

## 启动

在仓库根目录运行：

```bash
PYTHONPYCACHEPREFIX=/tmp/video-workbench-pycache \
python3 video-workbench/backend/app.py --host 127.0.0.1 --port 8765 \
  --projects-root "$PWD/work"
```

然后打开 <http://127.0.0.1:8765>。服务会优先托管 `video-workbench/frontend/dist/index.html`，否则托管 `video-workbench/frontend/index.html`。

也可以：

```bash
cd video-workbench
PYTHONPYCACHEPREFIX=/tmp/video-workbench-pycache python3 -m backend --port 8765
```

可选参数：

- `--data-root /path`：知识库、任务、事件、服务锁等工作台数据根目录。默认 `video-workbench/data`；也可使用环境变量 `VIDEO_WORKBENCH_DATA`。
- `--projects-root /path`：canonical 项目根目录，与知识库/任务数据分离；桌面启动时若仓库存在 `work/` 会默认使用它，也可用 `VIDEO_WORKBENCH_PROJECTS`。只登记该目录下一层且含 `project.json` 的项目，不复制大视频。
- `--skill-dir /path`：指定 `jimeng-video-remix-director` 安装目录。
- `--static-root /path`：指定前端静态目录。
- `VIDEO_WORKBENCH_DEBUG=1`：本地开发时在 500 响应中显示异常原因。

同一组 `data-root + projects-root` 同时只允许一个后端实例持有写锁。第二个实例会稳定返回/打印 `WORKBENCH_INSTANCE_ALREADY_RUNNING`，不会让两个进程同时改同一份 canonical JSON。正常关闭会等待任务线程安全退出并释放锁。

## 真实性和状态语义

- `completed`：当前明确 operation/执行档位的真实命令和门禁已通过。
- `waiting`：缺用户输入、项目没有显式启用 Codex、或 Word 仍需逐页视觉 QA；已有中间产物会保留。
- `blocked`：脚本、命令、依赖、结构 lint 或图文分支门禁未通过。
- `failed`：本地编排服务发生非预期错误。
- `paused` / `cancelled`：真实 subprocess 已在支持 POSIX 信号的系统暂停或终止。

`codex.enabled` 的项目默认值固定为 `false`。后端没有任意 shell API，也不接受客户端提交命令；只允许预定义导演脚本和安全的 Codex CLI 参数。Codex 使用结构化结果 schema，CLI 返回 0 也不会自动冒充完成：模型结果为 `waiting/blocked` 时，任务沿用该状态。

`full_delivery` 即使完成编译、导出和对齐，也会停在 `DOCX_VISUAL_QA_REQUIRED`，直到用户对当前 Word 的全部渲染页提交哈希绑定的视觉检查。`docx_export_authorized: true` 只是必要条件，不是充分条件：导出前还必须存在后端在当前 `verify` 后签发的 delivery preflight receipt，且源视频、配置、口播、镜头、标记、Prompt、检测、结果和审批哈希均未变化。

图片/视频后缀不会被当成媒体真实性。源视频、知识库图片/视频、即梦回传结果和 Word 渲染页都要同时通过 `ffprobe` 结构探测与 `ffmpeg` 实际解码；工具缺失返回 `MEDIA_VALIDATOR_NOT_AVAILABLE`，伪装后缀或损坏内容返回 422，失败文件不会进入任何 manifest。

## 核心响应

成功响应都含 `ok: true`。错误固定为：

```json
{
  "ok": false,
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "Human readable message",
    "details": {}
  }
}
```

### Bootstrap 和项目

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/bootstrap` | 服务能力、项目列表、产品库、人物库 |
| GET | `/api/v1/projects` | 项目列表 |
| POST | `/api/v1/projects` | 创建并实际调用 `init_project.py`；JSON 至少含 `name` |
| GET | `/api/v1/projects/:id` | 完整项目详情 |
| GET | `/api/v1/projects/:id/status` | 轻量轮询状态、六类 revision token 和 active task 摘要；不扫描/返回全量媒体 |
| PUT/PATCH | `/api/v1/projects/:id/config` | 保存产品、人物、镜头范围、执行档位和任务模式 |
| POST | `/api/v1/projects/:id/bindings/apply` | 把当前产品/人物选择真实写入 canonical 项目；不会把“已选择”冒充“已绑定” |

项目详情包含：

- `shots`：扁平的最小交付 unit 列表。每个 `source_units` / `inserted_units` 单独出现，`id` 为 `SRC…` / `ADD…`，保留 `delivery_asset_ids`；不会把一个 `S` 组合段误算成一张图。
- `shot_groups`：原始 `S…` 组合生成段。
- `script`、`markers`、`assets`、`generation_status`、`detection_results`、`docx_qa`。
- 只读 canonical 状态：`workflow`、`story_plan`、`alignment`、`asset_reuse_plan`、`active_revocation`、`active_workbench_revocations`、`docx_export_authorized`。

项目配置形状：

```json
{
  "product_mode": "preserve",
  "product_id": null,
  "character_mode": "preserve",
  "avatar_id": null,
  "source_person_id": null,
  "shot_scope": {"mode": "all"},
  "execution_tier": "source_intake",
  "task_mode": "single",
  "script_locked": false,
  "prompt_length_contract": {
    "enabled": false,
    "minimum_non_whitespace_characters": 0,
    "maximum_non_whitespace_characters": 0
  },
  "codex": {"enabled": false, "model": null}
}
```

允许值：

- `product_mode`: `preserve | replace`
- `character_mode`: `preserve | head_replace | full_replace`
- `source_person_id`：换头/换人物时绑定原片中的明确人物；多人物或无法解析时必须由用户选择，未知人物 ID 会拒绝。
- `shot_scope`: `{mode:"all"}`、`{mode:"range",start,end}` 或 `{mode:"selected",shot_ids:[...]}`
- `execution_tier`: `source_intake | diagnose_only | first_frame_only | prompt_only | full_delivery`
- `task_mode`: `single | dual`
- `prompt_length_contract`：唯一事实源最终写入 `project.json.prompt_length_contract`。默认 `false/0/0`，上下限都不检查并要求最短可执行 Prompt；只有用户在项目设置显式启用时，正整数 min/max 才同时成为硬门。`true/0/0` 成对归一为 `3000/4000`，单边缺失、布尔/浮点、负数或 `max < min` 整体拒绝。读取旧项目只投影，不静默改 canonical；显式修复会生成完整失效回执。若旧合同畸形，所有会编写、校验或交付 Prompt 的任务都会以 `PROMPT_LENGTH_CONTRACT_INVALID` 阻断，不能把无效合同静默降级成“关闭”。普通自动保存同时排除 `execution_tier` 和长度合同，只有“项目设置”显式保存才可改写这两个项目级事实。

普通 `task_mode` / Codex 开关保存不会重写 `project.json`。Prompt 长度合同或交付档位变化会进入 generation input SHA，并统一让旧 Prompt、检测、批准结果及 DOCX 授权失效。Codex 文线与总控都收到同一合同：关闭时明确禁止为了 3000–4000 扩写/堆水词；开启时按实算非空白字符执行双边硬门，仍不得绕过拆镜、情绪表演或证据覆盖。

### 视频上传和媒体

`POST /api/v1/projects/:id/video` 使用 `multipart/form-data`，文件字段固定为 `video`。兼容别名 `/source`。

成功返回：

```json
{
  "ok": true,
  "project": {},
  "video": {
    "filename": "source.mp4",
    "size": 123,
    "sha256": "...",
    "metadata": {
      "status": "ready",
      "duration": 31.2,
      "width": 1080,
      "height": 1920,
      "fps": 30.0,
      "has_audio": true
    },
    "video_url": "/api/v1/projects/.../media/source/uploads/...mp4",
    "thumbnail_url": "/api/v1/projects/.../media/source/thumbnails/...jpg"
  }
}
```

`GET/HEAD /api/v1/projects/:id/media/:relative-path` 只允许读取该项目目录内文件，并支持单段 HTTP `Range`，可用于 `<video>` 拖动播放。任何绝对路径、`..` 和 symlink 越界都会被拒绝。

当新视频 SHA-256 与旧视频不同，后端会持久化 `workbench/input-invalidations/*.json` replacement receipt，并立即将旧口播确认、镜头分析、Prompt、检测、对齐、审批有效性、交付图和 Word 授权统一标为 stale/false。旧文件保留作审计，但不能重新批准或导出；不能靠把 workflow 布尔值改回 `true` 绕过。

### 口播和手工画面标记

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/api/v1/projects/:id/script` | 真实持久化口播 |
| GET/POST | `/api/v1/projects/:id/markers` | 读取/添加吃食、掰开时间点 |

口播 PUT 字段：`source_text`、`revised_text`、`active_source: source|revised`、`locked`、`language`、`shot_mapping`。锁定时 active 文本不能为空。

标记 POST：

```json
{"kind":"eating","time":4.25,"shot_id":"SRC03","note":"牙齿首次接触"}
```

`kind` 只允许 `eating | breaking`，时间不能越过已上传视频时长；若提交 `shot_id`，它必须是当前最小 unit，且时间必须落在该 unit 内。标记变化会 bump 输入 revision，使旧 Prompt、检测、审批和 Word 失效。

### 产品/人物知识库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/knowledge` | 合并内置和自定义产品/人物 |
| GET | `/api/v1/knowledge/:kind/:id` | 读取单条产品或人物；`kind=products|avatars` |
| POST | `/api/v1/knowledge/products` | 上传产品参考 |
| POST | `/api/v1/knowledge/avatars` | 上传人物参考 |
| PATCH/PUT | `/api/v1/knowledge/:kind/:id` | 编辑自定义产品/人物元数据；内置条目只读 |

上传使用 `multipart/form-data`：

- 一个或多个文件字段：`file`
- 公共文本字段：`id`、`name`、`authorized`、`authorization_scope`、`usage_scope`、`notes`
- 产品结构字段：`dimensions_cm`、`package_spec`、`packaging_contracts`，值是 JSON object 字符串；不接受数组、`NaN` 或 `Infinity`
- 单文件参考维度：`reference_id`、`role`、`label`、`angle`、`product_state`、`packaging_layer`
- 多文件逐项维度：固定字段名 `reference_metadata`，值是 JSON 数组，长度必须与重复 `file` 字段完全一致；每项只允许 `reference_id`、`role`、`label`、`angle`、`product_state`、`packaging_layer`

多图产品上传示例（概念上的 FormData 顺序）：

```text
file = front.png
file = side.png
id = P-MY-PRODUCT
name = 我的产品
dimensions_cm = {"width":10,"height":18,"depth":4}
package_spec = {"present":true,"quantity":6,"topology":"rectangular_box","text_layout":{"front":["brand","product_name"]}}
reference_metadata = [{"reference_id":"REF-FRONT","role":"package_front","angle":"front"},{"reference_id":"REF-SIDE","role":"package_side","angle":"right"}]
```

上例的 `package_spec` 是向后兼容的扁平 v1 合同。新产品应把产品主体和包装层分开：顶层 `dimensions_cm` 只描述裸产品主体；`packaging_contracts` 按层描述包装；每张包装参考图在 `reference_metadata[*].packaging_layer` 中声明自己属于哪一层。支持的固定层名为：

| `packaging_layer` | 含义 | 常见 `contains` |
|---|---|---|
| `individual_package` | 直接包住单个产品的独立袋、膜或小包 | `product_body` |
| `retail_box` | 面向消费者的零售盒/包装盒 | `individual_package` 或 `product_body` |
| `inner_tray` | 零售盒内的内托、槽位托盘 | `individual_package` 或 `product_body` |
| `shipping_carton` | 装载零售盒的运输箱/外箱 | `retail_box` |

四层包装上传的完整 multipart payload 示例（每个 `file` 与 `reference_metadata` 同索引绑定）：

```text
file = product-body.png
file = individual-package-front.png
file = retail-box-front.png
file = inner-tray-top.png
file = shipping-carton-front.png
id = P-MY-LAYERED-PRODUCT
name = 我的四层包装产品
dimensions_cm = {"length":7,"width":2.5,"height":1.2}
packaging_contracts = {"individual_package":{"dimensions_cm":{"length":8,"width":3,"height":1.5},"quantity":1,"topology":"sealed_flow_wrap","contains":"product_body","material":"metallized_film"},"retail_box":{"dimensions_cm":{"length":18,"width":12,"height":7},"quantity":6,"topology":{"shape":"rectangular","closure":"reverse_tuck"},"contains":"individual_package","text_layout":{"front":["brand","product_name"]}},"inner_tray":{"dimensions_cm":{"length":17,"width":11,"height":4},"quantity":6,"topology":"six_cell_molded_tray","contains":"individual_package"},"shipping_carton":{"dimensions_cm":{"length":42,"width":36,"height":28},"quantity":12,"topology":"regular_slotted_carton","contains":"retail_box"}}
reference_metadata = [{"reference_id":"REF-PRODUCT","role":"product_whole"},{"reference_id":"REF-WRAP","role":"individual_package_front","packaging_layer":"individual_package","angle":"front"},{"reference_id":"REF-BOX","role":"retail_box_front","packaging_layer":"retail_box","angle":"front"},{"reference_id":"REF-TRAY","role":"inner_tray_top","packaging_layer":"inner_tray","angle":"top"},{"reference_id":"REF-CARTON","role":"shipping_carton_front","packaging_layer":"shipping_carton","angle":"front"}]
```

独立编辑接口使用 `application/json`，不要求重新上传图片。公共可编辑字段为 `name`、`version`（1–100 个单行可见字符）、`notes`、`authorized`、`authorization_scope`、`usage_scope`；产品另外支持顶层产品主体 `dimensions_cm`、兼容旧数据的 `package_spec` 和四层 `packaging_contracts`。参考图元数据按已经存在的 reference ID 做部分更新：

```json
{
  "expected_revision": 5,
  "name": "新版产品名",
  "version": "2",
  "notes": "产品主体和每层包装分别锁定",
  "authorized": true,
  "dimensions_cm": {"length": 7.2, "width": 2.6, "height": 1.25},
  "packaging_contracts": {
    "individual_package": {"dimensions_cm": {"length": 8, "width": 3, "height": 1.5}, "quantity": 1, "topology": "sealed_flow_wrap", "contains": "product_body"},
    "retail_box": {"dimensions_cm": {"length": 18, "width": 12, "height": 7}, "quantity": 6, "topology": "rectangular_box", "contains": "individual_package"},
    "inner_tray": {"dimensions_cm": {"length": 17, "width": 11, "height": 4}, "quantity": 6, "topology": "six_cell_tray", "contains": "individual_package"},
    "shipping_carton": {"dimensions_cm": {"length": 42, "width": 36, "height": 28}, "quantity": 12, "topology": "regular_slotted_carton", "contains": "retail_box"}
  },
  "reference_metadata": [
    {"id": "REF-PRODUCT", "label": "产品主体整根", "role": "product_whole"},
    {"id": "REF-BOX", "label": "零售盒正面母版", "role": "retail_box_front", "packaging_layer": "retail_box", "angle": "front"}
  ]
}
```

`reference_metadata` 是部分 patch：没出现在数组中的参考图完整保留；每一项没出现的元数据字段也保留。产品 reference 显式传 `"packaging_layer": null` 可清除旧的包装层归属；省略该字段则保留旧值。客户端不能通过该接口修改或删除 reference 的 `id`、`filename`、`original_filename`、`size`、`sha256`、`media_metadata`、`created_at`，后端会在写入前后强制核对这些字段，图片字节也不会被重写。`expected_revision` 可选；传入后若不是当前 revision，返回 `409 KNOWLEDGE_REVISION_CONFLICT`，防止两个编辑窗口后保存的页面覆盖先保存的内容。内置条目返回 `409 BUILTIN_KNOWLEDGE_READ_ONLY`。

编辑响应除更新后的 `asset` 外还返回 `changed_fields`、`revision`、更新前后 record SHA，以及 `binding_impact`。项目在应用产品/人物时已经复制并锁定的合同与参考图继续以项目内不可变快照为准；知识库编辑不会回写旧项目。`projects_requiring_explicit_reapply` 列出需要用户明确“重新应用”后才采用新版知识的项目。绑定任务正在执行期间若知识源 revision 变化，提交阶段继续返回 `PRODUCT_BINDING_SOURCE_CHANGED`，不会把新旧两版事实混成一个合同。人物若撤销 `authorized`，冻结合同仍保留审计哈希，但当前授权闸门会返回 `PORTRAIT_AUTHORIZATION_REVOKED`，不会继续把旧授权当成有效。

每个 `packaging_contracts.<layer>` 的规则：

- `present` 可省略，后端会规范化为 `true`。`present=true` 时必须同时提供非空 `dimensions_cm`、正整数 `quantity` 和非空 `topology`。
- `dimensions_cm` 的每一个键必须对应正的有限数值。它锁定的是该包装层自身尺寸，不会覆盖产品主体的顶层 `dimensions_cm`。
- `quantity` 表示该包装层直接容纳的 `contains` 单元数量。例如零售盒装 6 个独立包就是 `retail_box.quantity=6`、`retail_box.contains="individual_package"`；运输箱装 12 个零售盒就是 `shipping_carton.quantity=12`。
- `topology` 可为非空字符串或结构化 object，用于锁定盒型、封口、槽位等几何关系。
- `contains` 可省略；如提供，只允许 `product_body` 或另一个支持的包装层，且不能指向自身。
- 可选字段为 `text_layout`、`material`、`attributes`、`notes`。`present=false` 只可保留说明性 `notes`，不能同时声明尺寸、数量、拓扑或其他物理事实，也不能上传该层参考图。

绑定时采用双向完整性门禁：每个 `present=true` 的层至少要有一张同层参考；有同层参考就必须有同层合同。缺合同返回 `packaging_<layer>_contract`，缺参考返回 `packaging_<layer>_reference`，声明不存在却上传参考返回 `packaging_<layer>_absent_but_referenced`。因此包装盒图不会再被当成产品主体图，独立包装、盒、内托和运输箱也不会互相混用。

`GET /api/v1/knowledge` 及上传响应会返回：

- `packaging_contracts`：规范化后的逐层事实合同。
- `packaging_assets`：后端根据参考元数据派生的只读映射，形如 `{"retail_box":[{...reference,"media_url":"/api/v1/..."}]}`；客户端不能直接上传或覆盖该字段。
- `packaging_layers`：按 `individual_package → retail_box → inner_tray → shipping_carton` 固定顺序列出有合同或参考的层。

角色名可写成精确层名或 `<layer>_<view>`，但新数据建议始终显式传 `packaging_layer`，避免仅凭角色猜层。旧 `package_spec`、`package_front` 以及历史 `retail_box_front` 条目仍按 v1 扁平合同读取，原合同哈希字段不变；只有显式 `packaging_contracts`、显式 `packaging_layer` 或其他明确的新分层角色才进入 v2。

整个多文件请求是一个原子事务：先验证所有元数据，再逐个真实解码；任一文件失败会恢复上传前目录，前面的成功文件也不会泄漏给读取端。不要对每张图分别请求后再自行拼接，这会失去原子性。

同一 `id` 再次上传不会冲突，而是追加到同一条目的 `references` 和 `media_urls`。相同 SHA-256 不重复添加。返回的 `media_url` 是第一张参考，`media_urls` 是全部参考。

自定义产品应用后由后端写入 `library/product_immutable_contract.json`。v1 锁定产品尺寸、是否有包装、包装数量、盒体拓扑、文字版面、每张参考的 ID/角色/路径/大小/SHA；v2 还逐层锁定 `packaging_contracts`、`packaging_assets`、存在层列表、主包装层以及每张层级参考的 SHA。Codex 只读派生可执行建议，不能改这些事实；`package_spec.present=false` 是合法的明确“无包装”v1 合同。自定义人物会写入 `planning/avatar_binding_lock.json`，锁定替换模式、原片人物、使用授权、正脸/全身参考集合与逐文件 SHA；换头和整人替换都必须有可验证正脸，整人替换还必须有全身参考。

### 任务、进度与事件

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/tasks` | 创建任务 |
| GET | `/api/v1/tasks?project_id=...` | 任务列表 |
| GET | `/api/v1/tasks/:id` | 当前状态、阶段、lane 和进度 |
| POST | `/api/v1/tasks/:id/start` | 启动或修复输入后重试 |
| POST | `/api/v1/tasks/:id/pause` | 暂停真实 subprocess |
| POST | `/api/v1/tasks/:id/resume` | 继续 |
| POST | `/api/v1/tasks/:id/cancel` | 终止 |
| POST | `/api/v1/tasks/:id/retry` | 克隆失败/等待任务并立即启动；保留原因和结构化覆盖 |
| GET | `/api/v1/tasks/:id/events?after=0` | JSONL 增量事件轮询 |
| POST | `/api/v1/projects/:id/shots/:shot_id/retry` | 单镜返工 |

任务创建 JSON：

```json
{"project_id":"20260825-demo","operation":"run","instruction":"可选补充要求"}
```

支持 operation：

- `run`：按项目 `execution_tier` 执行最小足够工作流；`task_mode=dual` 时图线和文线并行、总控复核合并。
- `analyze`：先运行 `extract_video_assets.py`，再按单/双任务模式做真实口播与分镜语义分析；Codex 未显式开启时保留抽帧和音频并停在 `waiting`，不把“只抽了素材”冒充完整原片分析。
- `extract_frames`：`extract_shot_frames.py`
- `lint | compile | verify`：`pipeline.py`
- `export_docx`：先 lint，再调用 `export_jimeng_docx.py`，随后等待视觉 QA。
- `align`：`align_exports.py`
- `codex`：项目显式开启后的单总控任务。
- `retry_shot`：从原始真实首帧和批准参考做指定镜头返工。
- `apply_binding`：自定义产品在项目开启 Codex 后编译结构化产品规范；完成后仍由后端确定性校验 profile、状态、产品库条目和已复制参考文件。

同一项目的 canonical 写任务使用 FIFO writer lease；后来的任务显示 `PROJECT_WRITER_BUSY` 和 `queue_position`，前一任务终态后自动唤醒最老等待者。取消正在同步收尾的任务会先进入 `cancelling`，不会提前释放 lease。进程重启后，旧 running 任务变为可审计 blocked，旧 writer waiter 恢复为 queued，不会永久卡住。

单镜返工可以提交：

```json
{
  "reason": "吃食动作没有咬断证据",
  "owner_lane": "image",
  "issue_codes": ["EATING_BITE_MISSING"],
  "user_overrides": {
    "emotion": "先忍住期待，咬断后眼神突然亮一下",
    "action_beats": ["牙齿接触", "短暂受阻", "清脆断裂", "闭嘴咀嚼"],
    "speech_transition": "咀嚼结束后再接口播"
  }
}
```

返工初始 lane 只创建责任 lane；重试会逐字保留 `reason`、`issue_codes`、三类 `user_overrides` 和已编译 instruction，不会在下一次请求中丢失。`apply_binding` 不允许普通 retry，因为它是不可变合同事务，必须重新显式应用。

快捷端点会创建并立即启动同名任务：

- `POST /api/v1/projects/:id/analyze`
- `POST /api/v1/projects/:id/shots/extract-frames`
- `POST /api/v1/projects/:id/lint`
- `POST /api/v1/projects/:id/compile`
- `POST /api/v1/projects/:id/verify`
- `POST /api/v1/projects/:id/export-docx`
- `POST /api/v1/projects/:id/align`

### 即梦结果回传、拆镜和当前批准图

`POST /api/v1/projects/:id/shots/:unit_id/results` 使用 `multipart/form-data`：

- 文件字段固定为 `file`，且恰好一个文件
- `kind`: `first_frame | video`
- `version`: 用户可读版本，如 `v3`
- `notes`: 可选返工说明

后端会真实解码媒体，记录 `shots/results/result_manifest.json`、文件 SHA、尺寸/时长和上传时的 generation input contract。首帧不是约 9:16 时返回 warning 但不替用户武断拒绝；伪装图片/视频会整体回滚。上传不等于批准，必须再调用 approval 接口。项目 `generation_status.units[unit_id]` 只投影当前输入下最新、未撤销、哈希仍匹配的用户批准首帧；旧版保留历史但不计入 ready。

过长镜头采用两步确认，避免一次点击直接改 canonical：

```text
POST /api/v1/projects/:id/shots/SRC01/split-plan
{"cursor_time":2.15,"labels":["咬下并脆断","闭嘴咀嚼后说话"],"reason":"四秒镜头含两个动作拍"}

POST /api/v1/projects/:id/shots/SRC01/split-plan/confirm
{"plan_id":"split-..."}
```

第一步只写 `planning/split-plans/*.json`，并返回 `canonical_changed:false`。确认时用原 manifest 和原 unit 双 SHA 防止过期计划，备份原 manifest/config/markers，再以 `.a/.b` 替换一个 unit。若配置是 selected scope，会把旧 ID 原位替换成两个新 ID；吃食/掰开 marker 按时间重绑到对应子镜。子镜不继承父镜 Prompt、action beats、emotion 或 script mapping，`requires_regeneration` / `requires_semantic_reanalysis` 保持 true；仅 marker 明确证明的 `eating` 或 `breaking` 标签回填到对应一段，不能让两段都假装同时“吃·掰”。SRC 子镜的 `timeline_timecode` 始终精确读取各自 `source_timecode`，不会回退成父组全长。

### 检测合同

检测任务使用 `operation=codex`，instruction 中明确列出，例如 `detectors:eating,breaking`。后端把请求 detector 和创建时 shot scope 固化进任务合同，并强制 Codex `read_only`。每个 requested detector × scoped unit 必须恰好返回一次：

```json
{
  "detector": "eating",
  "unit_id": "SRC03",
  "result": "issue",
  "code": "EATING_BITE_MISSING",
  "severity": "error",
  "owner_lane": "image",
  "message": "没有看到牙齿接触和脆断证据",
  "evidence_time": 4.25,
  "evidence_asset": "source/frames/frame-004250.png",
  "evidence_asset_sha256": "64位十六进制摘要"
}
```

`pass | issue` 必须同时有证据时间、项目内相对文件路径和匹配当前文件的 SHA；时间必须落在该 unit。不能验证就只能写 `not_observable`。缺 pair、重复 pair、额外 detector、未知 unit、越界时间、缺文件或错 SHA 都拒绝整个检测 artifact。结果保存源视频、镜头、口播、产品/人物绑定、marker 和 detector task contract 输入哈希；任一变化后详情会返回 `stale:true`、`findings_are_effective:false` 和具体 stale reasons。

### 原片分析与依赖失效

`analyze/run` 的语义结果只有通过后端 analysis contract 才能完成：

- `shots/shot_manifest.json.source_video_sha256` 必须等于当前源视频；
- 最小 SRC/ADD unit 从 0 秒开始，无缝、无重叠覆盖到视频末尾；
- 默认单 unit 最长 3.5 秒，超长必须有 `duration_exception_reason` / `long_take_reason` / `action_beat_reason`；
- 吃食、掰开 marker 必须绑定到唯一且带同名 semantic tag 的最小 unit；
- 有人物的 unit 必须绑定 role lock 中的 source person；
- 每个 unit 必须有 script segment/shot mapping，或显式声明 silence；
- split 后等待局部语义重分析的 unit 不能通过。

源视频 SHA、active script、产品/人物/原片人物、shot scope、Prompt 长度合同、marker 或拆镜发生实质变化时，统一 invalidation transaction 会写 `workbench/input-invalidations/*.json` 和 `workbench/dependency_state.json`。旧 Prompt、检测、对齐、批准图和 Word 不删除，但都从有效投影中移除；新回传结果记录当前 generation input SHA，旧输入生成的结果会被 `STALE_GENERATED_ASSET_INPUTS` 阻止重新批准。

### Word 导出和逐页 QA

Word 放行有两道独立哈希门：

1. 当前 `verify` 成功后，后端写 `review/delivery-preflight-receipts/*.json`；`export_docx` 同时要求 canonical `docx_export_authorized:true` 和 receipt 中的 delivery input SHA 与当前完全相同。
2. 导出后，文档渲染流程写 `review/docx_render_manifest.json`，用户逐页检查并提交 `POST /api/v1/projects/:id/docx-qa`。

render manifest：

```json
{
  "document_path": "exports/delivery.docx",
  "document_sha256": "...",
  "pages": [
    {"page": 1, "path": "review/docx-render-pages/page-001.png", "sha256": "..."}
  ]
}
```

QA 请求：

```json
{
  "decision": "approve",
  "document_sha256": "当前 Word SHA",
  "page_sha256s": ["第1页SHA", "第2页SHA"],
  "reason": "逐页确认无漏图、无裁切、顺序正确"
}
```

页面必须从 1 连续编号、每张都可真实解码，提交的有序 SHA 列表必须覆盖全部当前页面。`reject` 必须写原因并撤销导出授权。即使 workflow 仍写 `passed`，只要 Word、任一渲染页或 QA receipt 不再与当前哈希一致，详情也会变成 `DOCX_QA_RENDER_STALE` / `DOCX_QA_RECEIPT_STALE`，不能交付。

### 审批和撤销

`POST /api/v1/projects/:id/approvals`：

```json
{
  "asset_path": "shots/approved/SRC03.png",
  "asset_id": "ASSET-SRC03-01",
  "shot_id": "SRC03",
  "decision": "approve",
  "reason": "总览确认"
}
```

`decision=approve` 的有效性同时依赖：文件 SHA 未变、当前输入合同一致、真实用户 ledger receipt 完整、未被撤销。普通模型在 inventory 里写 `approved` 不算用户批准；canonical 历史只有明确 `user_approved` 才可作为旧式用户批准，而且一旦发生 material invalidation 就不能继续沿用。

`decision=revoke` 会先由后端确定性地把 `docx_export_authorized=false`、Word QA 设为 invalidated、alignment 设为 stale，再保存 active revocation 并调用 `invalidate_revoked_delivery.py`。因此即使脚本缺失或失败，旧 Word 也已经 fail-closed；`cascade.status=blocked` 只表示 canonical 级联仍需修复，不会声称全部同步完成。被撤销的同一路径/ID/SHA 不能靠普通 approve 恢复，必须回传不同字节的新版本；同一 shot 的新版本批准后旧 workbench revocation 标为 superseded，但 Word 仍需重新生成和重新逐页 QA。

## 本地安全边界

- 默认只监听 `127.0.0.1`；API CORS 只接受当前实际端口的 `localhost/127.0.0.1/[::1]` Origin，不使用 `*`。
- 前端 `index.html`、JS、CSS 一律 `Cache-Control: no-store`，避免桌面工作台更新后仍加载旧逻辑；不可变知识库/Skill 媒体可以缓存。
- 项目媒体只从项目目录内读取；Skill 媒体只开放 `assets/`；旧项目绝对路径只有在已登记 SHA 且能唯一定位到项目内同后缀文件时才重定位。
- 不提供任意 shell、任意文件浏览或客户端命令执行 API。
- 项目 assets 扫描有目录和结果上限，不递归遍历历史 branch 大树；只补充 manifest 中已登记且哈希可核对的旧资产。

## 测试

```bash
PYTHONPYCACHEPREFIX=/tmp/video-workbench-pycache \
python3 -m unittest discover -s video-workbench/tests/backend -p 'test_*.py' -v
```

测试使用临时目录和假的 ffmpeg/导演脚本，不访问网络、不写真实知识库，也不会调用 Codex。当前套件覆盖媒体伪装、原子多图、不可变产品/人物合同、源视频统一失效、拆镜时码/语义/marker/scope、检测证据、任务 FIFO/取消竞态/重启恢复、撤销新版本、export 当前哈希门和 Word receipt stale；HTTP socket 测试在禁止 loopback bind 的 sandbox 中会明确 skip，而 Origin 策略仍以纯函数测试执行。
