# 本地视频工作台架构

## 目标

用独立网页管理项目，不把素材、进度和规则塞在一个聊天框里。UI 与 Skill 共用项目 JSON，任何模型都只是执行层。

## 分层

1. Web UI：项目列表、素材导入、字幕策略、故事结构、分镜时间线、首帧/美观帧、Prompt 检查、知识库、数字人库和导出。
2. 本地编排服务：读写项目目录，调用本 Skill 的 Python 脚本，管理任务状态、日志、版本和文件哈希。
3. AI 适配层：按功能分别调用 LLM、转写、图片编辑、数字人或视频生成服务。
4. 项目存储：本地文件为主；团队协作时可迁移到对象存储和关系数据库。

## API 边界

本地编排服务至少提供：

- `POST /projects`：创建项目。
- `POST /projects/:id/source`：导入原视频。
- `PUT /projects/:id/script`：保存字幕稿。
- `POST /projects/:id/analyze`：分析视频与字幕。
- `POST /projects/:id/shots/extract-frames`：提取每镜首帧和美观候选帧。
- `PUT /projects/:id/story-plan`：保存声音、占比和节奏方案。
- `POST /projects/:id/lint`：运行结构检查。
- `POST /projects/:id/compile`：生成 Prompt 和生成包。
- `GET/PUT /knowledge`：维护知识库。
- `GET/PUT /avatars`：维护数字人库。

## API 使用策略

UI 到本地编排服务必须使用 API；这能稳定任务状态并避免浏览器直接访问任意文件。外部 AI API 按需使用：LLM 用于语义分析和 Prompt，语音 API 用于转写/配音，图片 API 用于首帧替换，视频 API 用于支持的生成平台。某个平台没有稳定官方 API 时，保留人工提交或受控浏览器操作适配器，不把平台耦合进项目数据。

## 商业稳定性

保存每次输入、模型/服务版本、知识库命中、Prompt 哈希、输出路径、错误和操作人。密钥只保存在本地服务或服务器环境变量，不进入浏览器和项目 JSON。长任务使用队列和可恢复状态，不依赖单次网页请求。
