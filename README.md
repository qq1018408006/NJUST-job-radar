# Job Radar · 求职雷达

Self-hosted campus recruitment inbox with per-user reading states and AI employer research powered by Codex, Claude Code, or OpenCode.

一个可自托管、支持多账户的招聘信息收件箱。它将南京理工大学就业服务平台的公开宣讲会和招聘会（双选会）数据定时保存到本地 PostgreSQL，记录首次发现、内容版本、已读、收藏、隐藏和备注，并提供筛选、排序与一次性 AI 单位研究。

当前版本 `0.2.0`，使用 MIT 许可证。已内置南京理工大学的宣讲会和招聘会两个数据源；独立“招聘公告/岗位”栏目及其他高校尚未接入，外部数据可通过受保护的批量导入 API 接入。

## 功能

- 每天三次自动同步，也可从页面、命令行或 API 手动同步。
- 宣讲会与招聘会使用独立数据源记录，可在页面中分别筛选，互不影响失效判定。
- 以来源记录 ID 幂等更新，不会因为重复抓取生成重复数据。
- 区分新出现、读过后有更新、未读、即将举办、已结束、收藏和隐藏。
- 可将昨天及以前的未读批量整理为已读，前端连续两次确认且服务端校验确认标记。
- 支持关键词、线上/线下、来源、举办日期和多种排序方式。
- 内容变化时递增版本号；用户读到哪个版本会被单独保存。
- 每次有效内容更新都会保存字段级差异，详情页以“原来 → 现在”展示最近 20 次变化。
- 已读版本、收藏、隐藏和私人备注均按登录账户隔离保存，多用户之间互不影响。
- 来源状态单独在“未举办、进行中、已举办”之间流转时只更新状态，不生成新的内容版本或“内容更新”提醒。
- 来源连续缺失三次后标记失效，历史数据不会被删除。
- 新条目可自动创建一次性 AI 单位研究任务，由宿主机 Codex（默认）、Claude Code 或 OpenCode 联网检索后保存快照。
- 管理后台可按首次入库日期或举办日期批量建立 AI 任务，并可选择补齐缺失快照或覆盖全部总结；确认前不会调用模型。
- AI 快照展示研究工具、模型、生成时间、来源与真实输入/输出 Token，用量未报告时显示未记录。
- PostgreSQL 不暴露宿主机端口，写入 API 使用 Token 或登录会话鉴权。

## 目录与容器

项目只需要两个容器：

- `app`：Django、Gunicorn、页面、API、定时同步和管理后台，容器内监听 8000。
- `db`：PostgreSQL 17，仅连接 Docker 内部网络，数据保存到 `postgres_data` 卷。

实现与验收清单见 [TODO.md](TODO.md)。

```text
config/                    Django 配置
radar/                     模型、网页/API、同步与测试
templates/                 页面模板
prompts/employer_research.txt  三个后端共用的研究 Prompt
research.schema.json       研究输出结构
tools/ai_research_worker.py 通用宿主机 Worker
tools/research_providers.py CLI 适配与结果校验
tools/package_release.py   生成干净开源目录及部署包
deploy/                    用户级 systemd 服务及定时器
```

## 首次部署

服务器需已安装 Docker Engine 和 Docker Compose v2。

```bash
cp .env.example .env
```

编辑 `.env`，至少修改：

- `DJANGO_SECRET_KEY`
- `DJANGO_SUPERUSER_PASSWORD`
- `APP_API_TOKEN`
- `POSTGRES_PASSWORD`
- `ALLOWED_HOSTS`：填写服务器 IP 或域名，多个值用逗号分隔
- `CSRF_TRUSTED_ORIGINS`：使用 HTTPS 域名时填写完整来源，例如 `https://jobs.example.com`

数据库密码请使用 URL 安全字符（字母、数字、`-`、`_`）。随后启动：

```bash
docker compose up -d --build
docker compose logs -f app
```

默认访问地址：`http://服务器地址:8080`。端口可通过 `.env` 中的 `WEB_PORT` 修改。

首次启动会自动：

1. 等待 PostgreSQL 健康；
2. 执行数据库迁移；
3. 收集静态资源；
4. 创建环境变量指定的管理员；
5. 启动定时器和 Web 服务；
6. 在 `RUN_SYNC_ON_STARTUP=true` 时立即完成首次同步。

首次管理员只会在账号不存在时创建，容器重启不会重置其密码。

## 推荐的 HTTPS 配置

本 Compose 不额外捆绑反向代理，以保持最小两容器结构。公网部署应在宿主机或现有网关使用 Caddy、Nginx 或 Traefik 提供 HTTPS，再将流量转发到 `127.0.0.1:8080`。

确认 HTTPS 转发正常后，在 `.env` 中启用：

```dotenv
SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
SECURE_HSTS_SECONDS=31536000
```

首次配置 HTTPS 时建议先保持 `SECURE_HSTS_SECONDS=0`，确认域名和证书无误后再启用 HSTS。

## 同步配置

默认时间为北京时间每天 `08:00、13:00、20:00`：

```dotenv
TIME_ZONE=Asia/Shanghai
SYNC_SCHEDULE=08:00,13:00,20:00
```

手动同步：

```bash
docker compose exec app python manage.py sync_source njust
```

`njust` 会同步全部南京理工数据，也可只同步其中一类：

```bash
docker compose exec app python manage.py sync_source njust-91job
docker compose exec app python manage.py sync_source njust-91job-recruitment
```

查看最近日志：

```bash
docker compose logs --tail=200 app
```

默认只同步列表字段，以减少对来源网站的请求。设置 `FETCH_DETAILS=true` 会为每条记录额外请求详情；首次同步可能产生数百次请求，不建议长期默认开启。

## 一次性 AI 单位研究

AI 研究采用“容器内任务队列 + 宿主机 CLI Worker”。招聘同步不会等待 AI；只有在 `AI_RESEARCH_ENABLED=true` 之后首次入库的条目会自动创建任务，已有数据不会自动回填，内容更新也不会重复研究。后台的日期范围补齐/覆盖任务使用同一个队列。

宿主机安装并登录至少一种 CLI。默认使用 Codex，在 `.env` 中设置：

```dotenv
AI_RESEARCH_ENABLED=true
AI_RESEARCH_PIPELINE_VERSION=1
AI_RESEARCH_MAX_ATTEMPTS=3
AI_RESEARCH_LEASE_MINUTES=45
AI_RESEARCH_PROVIDER=codex
AI_RESEARCH_CODEX_BIN=codex
AI_RESEARCH_MODEL=
AI_RESEARCH_TIMEOUT_SECONDS=1800
JOB_RADAR_BASE_URL=http://127.0.0.1:8080
```

`JOB_RADAR_BASE_URL` 的端口需与 `WEB_PORT` 对应。环境变量优先于 `.env`，命令行选项优先于两者。systemd 的 PATH 可能与终端不同，必要时将对应 `*_BIN` 改为 CLI 的绝对路径。

切换工具只需修改 `AI_RESEARCH_PROVIDER`：

| 取值 | 工具 | 命令路径配置 | 专用模型配置 |
|---|---|---|---|
| `codex`（默认） | Codex CLI | `AI_RESEARCH_CODEX_BIN` | `AI_RESEARCH_CODEX_MODEL` |
| `claude` | Claude Code | `AI_RESEARCH_CLAUDE_BIN` | `AI_RESEARCH_CLAUDE_MODEL` |
| `opencode` | OpenCode | `AI_RESEARCH_OPENCODE_BIN` | `AI_RESEARCH_OPENCODE_MODEL` |

专用模型设置优先于通用 `AI_RESEARCH_MODEL`，全部留空时采用 CLI 默认。OpenCode 显式模型必须使用 `provider/model` 格式，可通过 `opencode models` 获取。模型认证由相应 CLI 负责；Worker 不复制账号配置或安装 CLI。切换工具影响下一次执行的任务，已有快照保持原样；需要重做时使用后台覆盖总结。

三个后端的执行方式：

- Codex：`codex exec`，实时网页搜索、只读沙箱、临时会话、JSON Schema 输出。
- Claude Code：`claude --print`，只开放 WebSearch/WebFetch、无交互审批、JSON Schema 输出，提取 `structured_output`。
- OpenCode：`opencode run --format json`，研究 agent 只允许 websearch/webfetch；Worker 启用 Exa 搜索并禁用自动分享。CLI 的 JSON 格式是事件流，最终答案通过 Prompt 约束并在本地按同一 Schema 校验。模型需要支持工具调用，搜索服务需可访问。

原生强制输出与本地校验不同，但三个后端均在完整结构与来源引用校验通过后才提交数据库，失败则进入现有重试流程。当前 OpenCode 适配面向具有上述命令和事件格式的版本，V2 等不兼容 CLI 应新增适配器。

官方接口参考：[Codex 非交互模式](https://developers.openai.com/codex/noninteractive)、[Claude Code 编程调用](https://code.claude.com/docs/en/headless)、[OpenCode CLI](https://docs.opencode.ai/docs/cli/)、[OpenCode 搜索工具](https://docs.opencode.ai/docs/tools/)。

先检查本机配置（不领取任务，也不调用模型）：

```bash
python3 tools/ai_research_worker.py --check
python3 tools/ai_research_worker.py --check --provider claude
python3 tools/ai_research_worker.py --check --provider opencode
```

单次执行及临时切换：

```bash
python3 tools/ai_research_worker.py
python3 tools/ai_research_worker.py --provider claude
python3 tools/ai_research_worker.py --provider opencode
```

旧命令 `python3 tools/codex_research_worker.py` 是兼容入口，也遵从 `AI_RESEARCH_PROVIDER`；已有定时服务无需重装即可切换。每次执行会重新读取 `.env`，修改工具、模型或 Prompt 后，下次执行自动生效。

研究 Prompt 位于 [prompts/employer_research.txt](prompts/employer_research.txt)。可修改此文件，或设置 `AI_RESEARCH_PROMPT_FILE=/absolute/path/to/prompt.txt`。模板使用 `$research_date`、`$item_json` 两个变量，字面美元符号写成 `$$`。输出约束在 [research.schema.json](research.schema.json)。它要求核验实际招聘主体、业务、隶属关系、财务口径和来源；没有求职者个人背景时，只提供一般求职关注点。Prompt/Schema 修改后不会自动重跑旧快照，可由后台覆盖重做，或递增 `AI_RESEARCH_PIPELINE_VERSION`。

安装当前用户的定时 Worker：

```bash
python3 tools/install_ai_worker_service.py
```

定时器每两分钟最多处理一条任务，没有任务时立即退出。服务器若没有长期用户会话，需要由管理员为部署用户启用 systemd lingering。检查状态：

```bash
systemctl --user status job-radar-ai-worker.timer
journalctl --user -u job-radar-ai-worker.service -n 100 --no-pager
```

### 从管理后台按日期批量研究

进入“管理 → AI 研究任务”，点击右上角“按日期批量 AI 研究”：

1. 选择日期口径和开始、结束日期；“首次入库日期”适合补齐某批刚抓到的数据，“举办日期”适合围绕招聘日筛选。
2. 默认不勾选“覆盖已有总结”，此时只处理当前版本尚无快照的条目；勾选后会处理范围内全部条目。
3. 点击“预览将处理的条目”。预览只查询数据库，不会调用 AI，并会逐条标记“首次总结”或“覆盖现有总结”。
4. 核对数量和条目列表，勾选最终确认后再加入队列。

已经等待或运行的任务不会重复创建；失败或已耗尽重试的任务会被重置后重新排队。覆盖模式不会在入队时删除已有快照，只有新调研成功返回后才原子替换，因此重跑失败不会让旧总结消失。Worker 每两分钟最多处理一条，一次加入大量条目会持续较久并消耗所选 AI 工具的用量。

Worker 从 CLI 的原始 usage 保存真实用量：Codex 使用 `turn.completed.usage`，Claude 使用结果中的 `usage`，OpenCode 汇总各步 `step_finish.tokens`。输入 Token 统一包含工具报告的缓存读取/写入，缓存读取另列；输出保留工具报告的口径，推理输出只有报告时才单列。不同工具的 Token 与计费口径不完全相同，不应直接用总 Token 比较费用。缺失值显示未记录，不根据文本长度估算。

部分 OpenCode 版本可能缺失最终 `step_finish`：如果最终 JSON 完整且通过校验，可以保存总结，但不把前几步的用量误报为总用量。错误事件、未完成工具调用或非法输出仍会进入失败重试。

只为指定条目建立一次任务并手工运行 Worker：

```bash
docker compose exec app python manage.py enqueue_research --today --limit 1
python3 tools/ai_research_worker.py
```

也可用 `--item-id <条目 ID>` 指定条目。数据库以 `条目 + 研究流程版本` 做唯一约束。网页与招聘材料作为不可信输入处理，页面显示生成时间、置信度和可复核来源。执行日志默认不保存，排障时可设置 `AI_RESEARCH_KEEP_EVENT_LOG=true`，日志写入忽略提交的 `work/`。

## API

除用户状态接口外，下列接口可以使用登录会话或 Bearer Token：

```text
GET  /api/v1/items/
POST /api/v1/sources/njust-91job/sync/
GET  /api/v1/sync-runs/
POST /api/v1/ingest/batch/
POST /api/v1/items/{id}/state/
POST /api/v1/research/tasks/claim/
POST /api/v1/research/tasks/{id}/complete/
POST /api/v1/research/tasks/{id}/fail/
```

触发南京理工同步：

```bash
curl -X POST \
  -H "Authorization: Bearer $APP_API_TOKEN" \
  https://jobs.example.com/api/v1/sources/njust-91job/sync/
```

通用导入示例：

```json
{
  "source": "another-campus",
  "source_name": "另一所学校",
  "base_url": "https://example.edu",
  "records": [
    {
      "source_item_id": "notice-1001",
      "title": "测试招聘信息",
      "company": "测试单位",
      "event_date": "2026-10-03",
      "mode": "offline",
      "location": "教学楼 A101",
      "source_url": "https://example.edu/notices/1001"
    }
  ]
}
```

单次最多导入 1000 条。唯一键是 `source + source_item_id`，同一记录再次导入时会根据规范化内容哈希决定是否生成新版本。

## 更新应用

先备份数据库，再替换代码或拉取新版本：

```bash
docker compose build --pull app
docker compose up -d
docker compose logs --tail=100 app
```

迁移由启动脚本自动执行。

## 备份与恢复

备份：

```bash
docker compose exec -T db pg_dump -U jobradar -d jobradar -Fc > jobradar.dump
```

恢复到空数据库：

```bash
docker compose exec -T db pg_restore -U jobradar -d jobradar --clean --if-exists < jobradar.dump
```

如果修改了 `POSTGRES_USER` 或 `POSTGRES_DB`，同步替换命令参数。不要只备份容器文件系统；数据库持久数据位于 Docker 卷中。

## 本地开发与测试

未设置 `DATABASE_URL` 时，Django 会使用项目目录下的 SQLite，仅用于开发测试：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py test
python manage.py runserver
```

生产运行必须使用 Compose 中的 PostgreSQL。

## 来源与使用边界

南京理工适配器当前使用宣讲会与招聘会页面自身的公开 JSON 接口，而不是解析 HTML。上游接口、字段或访问策略可能变化，因此保留了原始 JSON、同步日志和独立适配器。部署者仍应遵守来源网站的服务条款、robots 规则和合理访问频率；默认每天三次属于低频同步。联系人信息默认仅供登录用户查看，不建议把本站匿名公开到互联网。

## 开源与发布

从当前部署目录生成干净的 GitHub 源码目录和最小部署包：

```bash
python3 tools/package_release.py --output-dir ../releases
```

生成 `job-radar-0.2.0/` 和 `job-radar-0.2.0.tar.gz`。目录可直接作为新 GitHub 仓库根目录，压缩包解压后可按“首次部署”启动。脚本只收录指定根文件、源码扩展名和逐一批准的部署/提示词/CI 文件；不收录任意 JSON/TXT/YAML、嵌套隐藏目录、数据库及运行目录。压缩包不保留本机用户名或用户组。输出已存在时不会覆盖。

脚本还会比对本地 `.env` 中长度足够的密码、Token 与密钥值，这是补充防护，不是完整的密钥扫描器。公开前仍应检查源码和文档中的个人信息、执行密钥扫描，并核对 Git 暂存区、提交作者/邮箱及全部待推送历史；`.gitignore` 不会移除已跟踪文件或历史中的秘密。不要把真实凭据写进源码或示例。

仓库已包含 MIT License、[贡献说明](CONTRIBUTING.md)、[安全说明](SECURITY.md)、[更新记录](CHANGELOG.md) 和 GitHub Actions CI。CI 运行 Django 检查、测试、发布打包、Compose 配置校验与 Docker 构建，不调用付费 AI 或真实招聘接口。

新仓库应上传生成目录的内容，避免把部署数据和整个 Codex 工作区推送上去。MIT 授权针对项目代码，不意味着重新授权来源网站内容或第三方 CLI。
