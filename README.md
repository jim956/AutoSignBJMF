# 班级魔方自动签到（Web 版）

基于 Flask 的「班级魔方」GPS 自动签到服务：微信扫码登录后，在网页上配置班级、位置、签到时间和通知方式，服务会在后台按计划自动签到，并通过 pushplus 推送执行结果。

> 本项目仅供个人学习与自用，请遵守所在学校的相关规定。使用自动化工具签到可能违反平台条款，由此产生的一切后果由使用者自行承担。

## 功能特性

- **微信扫码登录**：网页展示二维码，扫码后自动识别姓名与账号下的全部班级，Cookie 由服务端保存，无需手工抓包。
- **多班级支持**：一个账号加入多个班级时，可勾选要为哪些班级签到；每个班级各生成一条任务，独立调度、独立通知。
- **灵活的时间配置**：
  - 每天可设置多个执行时间点，支持精确到秒；
  - 支持按星期单独覆盖（例如工作日 `07:30`、周末 `09:00`，也可以把某天设为不签到）；
  - 支持任务生效日期范围（开始/结束日期，留空表示长期）。
- **常驻后台调度**：内置 APScheduler，按 `Asia/Shanghai` 时区到点执行，无需系统 cron。
- **pushplus 通知**：每次执行后推送结果；成功走 `wechat` 通道，失败时额外发送 `voice` 通道。
- **Web 管理后台**：查看、筛选、修改、启用/停用、删除所有任务，并浏览最近 7 天的执行日志。
- **完善的日志**：控制台 + 滚动文件日志（`app.log` / `error.log`），每条请求有 `X-Request-ID`，敏感字段自动脱敏。
- **Docker 部署**：提供开箱即用的 `Dockerfile`。

## 技术栈

| 组件 | 说明 |
| --- | --- |
| Flask | Web 服务与 REST API |
| APScheduler | 后台定时调度 |
| SQLAlchemy + SQLite | 任务、日志与全局配置持久化 |
| requests + BeautifulSoup | 模拟登录、抓取页面、提交签到 |
| pushplus | 微信消息通知 |

## 项目结构

```text
AutoSignBJMF/
├── run.py                    # 启动入口（初始化日志、创建应用、启动服务）
├── app/
│   ├── __init__.py           # Flask 应用、路由与全部 API
│   ├── db.py                 # SQLite 连接、建表与轻量迁移
│   ├── models.py             # Task / TaskLog / Setting 数据模型
│   ├── qr_login.py           # 微信扫码登录会话与二维码轮询
│   └── scheduler.py          # 调度任务注册、执行与日志清理
├── utils/
│   ├── attendance.py         # 签到核心逻辑（探测签到项并提交 GPS）
│   ├── user_info.py          # 解析用户姓名与账号下的班级列表
│   ├── notification.py       # pushplus 通知发送
│   └── log_config.py         # 统一日志配置、脱敏工具
├── static/
│   ├── index.html            # 用户配置页（扫码、填表、提交任务）
│   └── manage.html           # 后台管理页
├── db/                       # 运行时生成，存放 web_signin.db
├── logs/                     # 运行时生成，存放 app.log / error.log
├── src/                      # uv 项目脚手架预留，当前未参与运行
├── requirements.txt
├── Dockerfile
└── pyproject.toml
```

## 快速开始

### 环境要求

- Python 3.12（推荐；Docker 镜像使用 `python:3.12-slim`，同样可运行）
- 可在本机访问 `https://bjmf.k8n.cn` 的网络环境

### 本地运行

```bash
# 1. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务（默认端口 8000）
python run.py
```

启动后访问：

- 用户配置页：<http://127.0.0.1:8000/>
- 后台管理页：<http://127.0.0.1:8000/manage>
- 健康检查：<http://127.0.0.1:8000/health>

如需修改端口，设置环境变量 `WEB_SIGNIN_PORT` 即可：

```bash
WEB_SIGNIN_PORT=9000 python run.py
```

### Docker 运行

```bash
# 构建镜像
docker build -t autosignbjmf .

# 运行（挂载 db 与 logs，避免容器重启后任务和日志丢失）
docker run -d --name autosignbjmf \
  -p 8000:8000 \
  -v "$(pwd)/db:/app/db" \
  -v "$(pwd)/logs:/app/logs" \
  --restart unless-stopped \
  autosignbjmf
```

> ⚠️ **不要使用多进程/多 worker 方式部署**（例如 `gunicorn -w 4`）。扫码会话与管理员 token 存在进程内存中，APScheduler 也只在当前进程内调度，多进程会导致任务重复执行、扫码状态查不到。

## 使用流程

1. 打开用户配置页，点击「获取二维码」，使用微信扫码并确认登录。
2. 扫码成功后会自动填好姓名，并列出账号下的全部班级，勾选需要签到的班级。
3. 填写签到位置经纬度（例如 `26.056756 119.196135`，支持空格、逗号、竖线等分隔符；也可使用页面上的定位按钮获取当前位置）。
4. 添加每天的执行时间点；如有需要，再为个别星期单独设置时间或设为不签到。
5. 可选：填写 pushplus Token 以接收签到结果通知，并设置任务生效的日期范围。
6. 提交任务。之后服务会在后台按计划自动签到，执行结果可在页面或 `/manage` 中查看。

> Cookie 会过期。当日志中出现「无法获取用户信息 / cookie 失效」时，重新扫码提交一次即可，新的 Cookie 会更新到原任务上。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WEB_SIGNIN_PORT` | `8000` | Web 服务监听端口（`1-65535`） |
| `LOG_LEVEL` | `INFO` | 日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_DIR` | `<项目>/logs` | 日志目录 |
| `LOG_TO_CONSOLE` | `1` | 是否输出到控制台 |
| `LOG_TO_FILE` | `1` | 是否写入日志文件 |
| `LOG_MAX_BYTES` | `10485760` | 单个日志文件大小上限（10 MB） |
| `LOG_BACKUP_COUNT` | `5` | 历史日志文件保留数量 |
| `LOG_APP_FILE` | `app.log` | 全量日志文件名 |
| `LOG_ERROR_FILE` | `error.log` | WARNING 及以上日志文件名 |
| `LOG_FORMAT` | 见 `utils/log_config.py` | 自定义日志格式 |
| `LOG_HTTP_DEBUG` | `0` | 设为 `1` 打开 urllib3 DEBUG（会包含完整 URL，可能带 token，慎用） |
| `BJMF_DEBUG_HTML` | `0` | 设为 `1` 时把未签到页面保存为 HTML，便于排查站点改版 |
| `BJMF_DEBUG_HTML_PATH` | `debug_html.txt` | 调试 HTML 的保存路径 |

## 数据存储

所有业务数据都保存在 SQLite 数据库 `db/web_signin.db` 中（首次启动时自动创建）：

| 表 | 内容 |
| --- | --- |
| `tasks` | 姓名、班级 ID/名称、Cookie、经纬度、pushplus Token、每天时间点、按星期时间点、生效日期范围、启用状态 |
| `task_logs` | 每次执行的状态与完整日志文本，仅保留最近 7 天（启动时和执行后自动清理） |
| `settings` | 全局配置（如管理员 Key 哈希） |

- 同一账号的同一班级只保留一条任务；重复扫码提交会**更新**已有任务，不会重复创建。
- 取消勾选的班级不会新建任务；已有任务需要到 `/manage` 删除或停用。
- **数据库中的 Cookie 为明文**，请妥善保管 `db/` 目录，不要提交到 Git 或打包进镜像（仓库已通过 `.gitignore` / `.dockerignore` 排除）。

## API 概览

### 用户侧

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/qr` | 创建扫码会话，返回 base64 二维码与 token |
| `GET` | `/api/qr/status?token=` | 轮询扫码状态（`pending` / `success` / `expired` / `error`） |
| `POST` | `/api/tasks` | 基于扫码 token 创建或更新签到任务 |
| `GET` | `/api/tasks/<id>` | 查看任务详情（不含 Cookie） |
| `GET` | `/api/tasks/<id>/logs?limit=` | 查看指定任务的执行日志 |
| `GET` | `/api/user/config?token=` | 基于扫码会话查询该账号最近一次任务配置 |
| `GET` | `/api/user/logs?token=&limit=` | 基于扫码会话查询该账号全部任务的日志 |

### 管理侧

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/admin/init` | 初始化管理员 Key（仅首次） |
| `POST` | `/api/admin/login` | 使用管理员 Key 换取 24 小时有效的 admin token |
| `GET` | `/api/admin/tasks` | 任务列表，支持 `name` / `class_id` / `enabled` 过滤 |
| `PATCH` | `/api/admin/tasks/<id>` | 修改位置、时间、通知 Token、启用状态、日期范围 |
| `DELETE` | `/api/admin/tasks/<id>` | 删除任务及其全部日志 |
| `GET` | `/api/admin/logs?task_id=&limit=` | 查看执行日志 |

> ⚠️ **安全提示**：当前版本为便于使用，`/manage` 及 `/api/admin/*` 的鉴权函数 `_require_admin` 直接放行，任何人都能查看和修改任务。请勿将服务直接暴露到公网；如需公网部署，请恢复 `app/__init__.py` 中的 token 校验，或在前置反向代理上加访问控制。

### 创建任务请求示例

```json
{
  "token": "<扫码登录 token>",
  "class_ids": ["127372", "130390"],
  "coord": "26.056756 119.196135",
  "pushplus_token": "<可选>",
  "times": ["07:30:00", "12:30:15"],
  "weekday_times": { "1": ["07:30:00"], "6": [], "7": ["09:00:00"] },
  "date_start": "2026-03-01",
  "date_end": "2026-03-31"
}
```

- `class_ids` 省略时默认给扫码识别到的全部班级各建一条任务。
- `weekday_times` 中键为 `1`（周一）~ `7`（周日）；空数组表示当天不签到；未出现的星期沿用 `times`。
- `date_start` / `date_end` 留空表示不限制日期范围。

## 日志与排查

- 日志默认写入 `logs/app.log`（全量）与 `logs/error.log`（WARNING 及以上），按 10 MB 滚动、保留 5 份。
- 每个 HTTP 响应都会带 `X-Request-ID` 头，可与日志中的 `request_id` 字段对应，便于串联一次请求的多行日志。
- Cookie、token、pushplus Key 等敏感信息在日志中会自动脱敏。
- 若扫码二维码页面结构变化导致无法获取二维码，可查看服务端日志中的提示，并按需设置 `BJMF_DEBUG_HTML=1` 保存页面 HTML 进行排查。

## 常见问题

**Q：扫码后一直显示 pending？**
确认服务所在网络能正常访问 `bjmf.k8n.cn`，并且微信已确认登录。二维码会话有效期为 120 秒，超时后重新获取即可。

**Q：日志提示 cookie 失效 / 跳过签到？**
Cookie 有有效期，重新扫码提交任务会更新 Cookie。若仍失败，可能是班级 ID 或站点页面结构发生变化，可开启 `BJMF_DEBUG_HTML=1` 检查。

**Q：重启后任务还在吗？**
在。任务保存在 `db/web_signin.db`，服务启动时会把所有 `enabled` 的任务重新注册到调度器。Docker 部署时请挂载 `db/` 和 `logs/` 目录。

**Q：为什么没有收到 pushplus 通知？**
检查任务中是否填写了 pushplus Token，以及服务端日志中 `pushplus 通知发送` 的结果。通知失败不会影响签到主流程。

## 致谢

- 参考项目：[JasonYANG170/AutoCheckBJMF](https://github.com/JasonYANG170/AutoCheckBJMF)
- 仅保留并简化了 GPS 签到相关功能，其他能力请参考原项目。