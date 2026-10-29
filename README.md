# 班级魔方多人GPS自动签到（Web 版）

- Thanks To [JasonYANG170/AutoCheckBJMF](https://github.com/JasonYANG170/AutoCheckBJMF) ，根据自己学校的签到进行了简化
- 仅根据自己学校的班级魔方需求更改简化代码,仅支持GPS签到(可在范围外)，其他功能请到项目[AutoCheckBJMF](https://github.com/JasonYANG170/AutoCheckBJMF)项目查看其他内容
- 可配置多人签到
- 可配置pushplus通知签到情况
- 如果你觉得好用,`Please Star`orz
  ![网页端展示](doc/img0.jpg)

## 功能

- 微信扫码登录，自动获取用户姓名、班级 ID 和班级码
- **支持一个账号加入多个班级**：扫码后会列出全部班级，勾选要签到的班级即可，每个班级分别创建一条任务独立执行
- 每个账号一条任务，可配置多个每天执行的时间点（支持到秒），也可限定任务生效的日期范围
- **支持按星期单独设置时间点**：例如工作日 07:30、周末 09:00，也可以把某一天设为不签到
- 常驻后台调度（APScheduler），到点自动签到，无需 cron / 计划任务
- 每次执行后单独推送 pushplus 通知（成功走 `wechat` 通道，失败走 `voice` 通道）
- Web 管理页查看、修改、启用/停用所有任务，并查看最近 7 天的执行日志
- 支持 Docker 部署

## 代码结构

```
AutoSignBJMF/
├── utils/                      # 签到核心逻辑（被 Web 侧复用）
│   ├── __init__.py
│   ├── user_info.py            # 获取用户与班级信息
│   ├── notification.py         # pushplus 通知
│   └── attendance.py           # 签到执行逻辑
└── web_signin/                 # Flask Web 管理端（唯一入口）
    ├── run.py                  # 启动入口，决定端口
    ├── app/
    │   ├── __init__.py         # 路由、API、应用初始化
    │   ├── db.py               # SQLite 连接与会话
    │   ├── models.py           # 数据表定义（tasks / task_logs / settings）
    │   ├── qr_login.py         # 微信扫码登录
    │   └── scheduler.py        # 定时任务注册与执行
    ├── static/
    │   ├── index.html          # 用户配置页
    │   └── manage.html         # 后台管理页
    ├── db/web_signin.db        # SQLite 数据库（任务数据的唯一存储）
    ├── Dockerfile
    ├── requirements.txt
    └── config.json.example     # 端口配置模板
```

## 数据存储

所有数据都存放在 SQLite 数据库 `web_signin/db/web_signin.db` 中，没有其他配置文件：

| 表 | 内容 |
| --- | --- |
| `tasks` | 姓名、班级 ID、班级名称、Cookie、经纬度、pushplus token、每天执行的时间点、按星期单独设置的时间点、生效日期范围、启用状态 |
| `task_logs` | 每次执行的结果与完整日志文本，仅保留最近 7 天（启动时和每次执行后自动清理） |
| `settings` | 全局配置（如管理员 Key 哈希） |

- 同一个账号的同一个班级只会有一条任务；重新扫码提交时是**更新**已有任务，不会重复创建。
- 一个账号加入多个班级时，**每个班级各有一条任务**，可以只勾选其中一部分；取消勾选的班级不会新建任务，已有的任务需要到 `/manage` 删除或停用。
- Cookie 有有效期，失效后重新扫码即可，新 Cookie 会覆盖旧任务里的那一条。
- 该数据库已加入 `.gitignore` / `.dockerignore`，不会随代码上传或打进镜像。

## 安装与运行

1. 安装依赖（在仓库根目录执行）：
   ```bash
   pip install -r web_signin/requirements.txt
   ```
2. （可选）在 `web_signin/` 下复制一份配置模板来自定义端口：
   ```bash
   cd web_signin
   cp config.json.example config.json    # Linux / macOS
   copy config.json.example config.json  # Windows
   ```
   默认端口为 `9988`，也可用环境变量 `WEB_SIGNIN_PORT` 覆盖（优先级最高）。
3. 启动服务：
   ```bash
   python web_signin/run.py
   ```
4. 在浏览器访问：
   - `http://127.0.0.1:9988/` — 用户配置页：微信扫码、填写经纬度与时间点、提交任务
   - `http://127.0.0.1:9988/manage` — 后台管理页：查看/修改/停用/删除所有任务与日志

### 使用流程

1. 打开配置页，用微信扫码登录，页面会自动带出姓名和该账号加入的全部班级。
2. 勾选需要签到的班级（默认全选），填写经纬度（格式随意，`30.123456 120.123456`、逗号分隔都可以），设置每天的签到时间点。
3. 可选：在「按星期单独设置」里给某个星期指定不同时间（例如工作日 `07:30`、周末 `09:00`），或把某天设为「不签到」；未单独设置的星期沿用第 2 步的时间点。
4. 可选：填写 pushplus token 接收该账号的通知；可选：设置任务生效的日期范围。
5. 提交后每个勾选的班级各写入一条任务并注册到调度器，到点自动执行，无需再手动运行任何脚本。

经纬度可参考 [高德地图坐标拾取器](https://lbs.amap.com/tools/picker)；pushplus token 在 [pushplus 官网](https://www.pushplus.plus/) 注册获取。

### 用 Docker 运行

```bash
docker build -t bjmf-web -f web_signin/Dockerfile .
docker run -d --name bjmf-web -p 9988:9988 \
  -v "$(pwd)/web_signin/db:/app/web_signin/db" \
  bjmf-web
```

或使用 `docker-compose.yml`（已配置好端口映射与数据库目录挂载，避免容器重建后任务丢失）：

```bash
docker compose up -d
```

如需调整端口，修改映射的 `-p 宿主端口:容器端口`，或通过环境变量 `WEB_SIGNIN_PORT` 覆盖容器内部端口（docker compose 会自动读取仓库根目录的 `.env`）。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `WEB_SIGNIN_PORT` | Web 服务端口，优先级高于 `web_signin/config.json` |
| `BJMF_DEBUG_HTML` | 设为 `1` 时，未找到签到项时把页面 HTML 落盘，用于排查站点改版 |
| `BJMF_DEBUG_HTML_PATH` | 上者落盘路径，默认 `debug_html.txt` |

## 更新说明

- 2026.10.29
  - **接手项目**
  - **新增按星期单独设置时间点**：`tasks` 表新增 `weekday_times` 列，格式 `{"1": ["07:30:00"], "7": []}`（键 1=周一 … 7=周日，空数组表示该天不签到）。未配置的星期沿用原有的每天时间点，因此**老任务行为不变**；老数据库启动时会自动补列，无需手动迁移
  - 调度器改为按「时间点 + 生效星期」注册 cron（例如 `mon,wed,fri`），同一时间点不会为每个星期重复建 job
  - **移除命令行版本**（`BJMF.py`、`auto_add_user.py`、`utils/config_manager.py`、`data.json`、`.env`、根目录 `requirements.txt`），数据统一存放于 SQLite，Web 端是唯一入口
  - 随之移除 CLI 独有的 `send_summary_notification`（多人汇总通知）：Web 端按任务执行，每次执行单独推送，不存在"整批跑完"的时机
  - `debug_html.txt` 改为仅在 `BJMF_DEBUG_HTML=1` 时生成，正常运行时不再在进程目录产生垃圾文件
  - 修复 `web_signin` 定时任务因 `sys.path` 缺少项目根目录而无法导入 `utils` 的问题（此前定时签到实际不会执行）
  - `web_signin/requirements.txt` 升级 SQLAlchemy 版本区间以支持 Python 3.13，并移除未使用的 `qrcode`
  - 修复后台管理页通知 Key 字段与后端 `pushplus_token` 不匹配、修改时间点后旧调度仍继续执行、重复提交会创建重复任务的问题
  - 日志时间改为按 Asia/Shanghai 记录；`config.json.example` 默认端口与 Docker 保持一致（9988）
  - **修复一个账号加入多个班级时只识别第一个班级的问题**：`utils/user_info.py` 改为解析 `/student` 页面上的全部班级卡片（原来的实现取到第一个 `/student/course/<id>` 链接就 `break`），扫码后前端列出所有班级供多选，每个班级各建一条任务；同时修复签到逻辑优先使用页面上的第一个班级、而不是任务自己的班级 ID 的问题。`tasks` 表新增 `class_name` 列（仅用于展示，老库启动时自动补列），`POST /api/tasks` 新增可选参数 `class_ids`，不传则默认识别到的全部班级

- 2026.09.01
  - 原作者学校的签到方式改变，本项目暂停维护，但功能依旧正常，如有需要可以自行二开优化使用。

- 2026.01.10
  - 修复 `utils/attendance.py` 的签到状态检测逻辑，增加对"已签到"状态的 HTML 解析，解决正则匹配失败导致的误报问题

- 2025.12.15
  - 更新 `utils/attendance.py`，改用 `requests.Session()` 防止获取签到项失败；增加对 `response.url` 的检测

## 注意事项

- **后台管理页 `/manage` 目前没有密码保护**，任何人都能查看和修改所有任务。请勿将本服务直接暴露到公网，需要对外时请自行加反向代理鉴权或恢复 `_require_admin` 的 token 校验。
- 班级信息在扫码时一次性写入任务；如需更换班级或账号，重新扫码提交即可。
- Cookie 具有时效性，签到失败并提示 Cookie 无效时，重新扫码更新凭证即可。
- `web_signin/db/web_signin.db` 中包含 Cookie、经纬度、推送 token 等敏感信息，请妥善保管；分享日志或截图时请注意打码。