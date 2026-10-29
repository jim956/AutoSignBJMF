import os
import re
import sys

# 通过 `python web_signin/run.py` 启动时，sys.path[0] 是 web_signin/ 目录，
# 项目根目录不在其中，调度任务里 `from utils.attendance import Task` 会导入失败。
# 这里显式把项目根目录加入 sys.path，保证无论在 web_signin 目录还是项目根目录启动都能导入 utils。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from flask import Flask, jsonify, request, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
import secrets
import hashlib
from datetime import datetime, timedelta

from .db import init_db
from .scheduler import (
    dump_weekday_times,
    has_any_execution,
    parse_times,
    register_existing_tasks,
    register_task_jobs,
    unregister_task_jobs,
)
from .models import Task, TaskLog, Setting
from .qr_login import QRLoginService
import json


qr_service = QRLoginService()


def _parse_coord_string(coord: str):
    """
    将前端传入的一整段经纬度字符串解析为 (lat, lng)。
    支持空格、逗号、竖线、中文逗号等多种分隔符。
    """
    if not coord:
        return None, None
    # 统一替换常见分隔符为空格
    normalized = re.sub(r"[,\|，、]+", " ", coord)
    parts = [p for p in normalized.strip().split() if p]
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


def _normalize_weekday_times(raw):
    """
    校验并规范化前端提交的按星期配置。

    输入形如 {"1": ["07:30:00"], "7": []}，键为 1(周一) ~ 7(周日)：
    - 值里的时间点统一成 HH:MM:SS，非法值丢弃；
    - 空数组表示「该天不签到」，需要保留；
    - 没有出现的星期表示沿用每天默认时间点。
    返回 (normalized_dict, error_message)。
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "weekday_times 必须是一个以星期为键的对象"

    normalized = {}
    for key, value in raw.items():
        try:
            weekday = int(key)
        except (TypeError, ValueError):
            continue
        if not 1 <= weekday <= 7:
            continue
        if not isinstance(value, list):
            return None, "weekday_times 中每个星期的值都必须是时间点数组"
        normalized[str(weekday)] = parse_times(value)
    return normalized, None


def create_app():
    app = Flask(__name__)

    # 初始化数据库
    engine, SessionLocal = init_db()
    app.config["DB_ENGINE"] = engine
    app.config["DB_SESSION_FACTORY"] = SessionLocal

    # 初始化 APScheduler（后台调度器）
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.start()
    app.config["SCHEDULER"] = scheduler

    # 简单的内存态管理员 token 管理（仅单进程场景）
    app.config["ADMIN_TOKENS"] = {}  # {token: expire_at_utc}

    # 启动时加载已启用任务到调度器
    register_existing_tasks(app)

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    # 前端单页（用户配置页面）
    @app.get("/")
    def index_page():
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        return send_from_directory(static_dir, "index.html")

    # 后台管理页面（仅提供静态 HTML，实际权限控制在 /api/admin/* 接口中完成）
    @app.get("/manage")
    def manage_page():
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
        return send_from_directory(static_dir, "manage.html")

    # ========== 阶段三：扫码登录接口 ==========

    @app.get("/api/qr")
    def api_create_qr():
        """
        创建扫码登录会话，返回二维码 base64 和 token。
        """
        token = secrets.token_urlsafe(16)
        result = qr_service.create_qr_session(token)
        if not result or result.get("error"):
            return jsonify({"error": result.get("error", "创建二维码失败")}), 500
        return jsonify(result)

    @app.get("/api/qr/status")
    def api_qr_status():
        """
        轮询扫码状态。
        返回：
        - pending: 尚未扫码/确认
        - success: 登录成功，返回用户与班级信息
        - expired: 二维码过期
        - error: 出错
        """
        token = request.args.get("token")
        if not token:
            return jsonify({"error": "missing token"}), 400

        state = qr_service.get_session_state(token)
        if not state:
            return jsonify({"status": "expired"})

        if state.error:
            return jsonify({"status": "error", "error": state.error})
        if state.expired:
            return jsonify({"status": "expired"})
        if state.cookie and state.user_name:
            return jsonify(
                {
                    "status": "success",
                    "user": {
                        "name": state.user_name,
                        "class_id": state.class_id,
                        "class_name": state.class_name,
                        "class_code": state.class_code,
                        # 账号下的全部班级，前端据此让用户选择要给哪些班级签到
                        "classes": state.classes or [],
                    },
                }
            )

        return jsonify({"status": "pending"})

    # ========== 阶段三：任务管理接口（用户侧） ==========

    @app.post("/api/tasks")
    def api_create_task():
        """
        基于已经完成扫码登录的会话创建签到任务。
        Body JSON:
        - token: 扫码登录 token
        - class_ids: ["127372", "130390"]（可选，要给哪些班级签到；
          每个班级各建一条任务，不传则默认识别到的全部班级）
        - coord: 一整段经纬度字符串（可包含空格、逗号、竖线等任意分隔符）
        - pushplus_token
        - times: ["07:30:00","12:30:15"]  （每天默认时间点，支持到秒，若仅到分钟将自动补齐为 :00）
        - weekday_times: {"1": ["07:30:00"], "7": []}（可选，按星期覆盖；
          键 1=周一 … 7=周日，空数组表示该天不签到，未出现的星期沿用 times）
        - date_start: "2026-03-01"（可选，任务开始生效日期）
        - date_end: "2026-03-31"（可选，任务结束日期，留空则表示长期）
        """
        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token:
            return jsonify({"error": "missing token"}), 400

        state = qr_service.get_session_state(token)
        if not state or not state.cookie or not state.user_name:
            return jsonify({"error": "invalid or expired token"}), 400

        # 兼容老版本：优先从 coord 解析，经纬度单独字段作为降级方案
        coord = (data.get("coord") or "").strip()
        lat = None
        lng = None
        if coord:
            lat, lng = _parse_coord_string(coord)
        # 如果 coord 解析失败，则尝试直接使用 lat / lng 字段
        if not lat or not lng:
            lat = data.get("lat")
            lng = data.get("lng")

        # 精度/海拔前端不再传，后端统一固定为 30
        acc = "30"
        times = data.get("times") or []
        pushplus_token = data.get("pushplus_token") or ""

        # 按星期单独配置：{"1": ["07:30:00"], "7": []}。
        # 未传该字段时（老页面 / 老客户端）沿用库里已有配置，避免把按星期设置清空。
        raw_weekday_times = data.get("weekday_times", "__missing__")
        weekday_times_provided = raw_weekday_times != "__missing__"
        normalized_weekday_times = None
        if weekday_times_provided:
            normalized_weekday_times, weekday_error = _normalize_weekday_times(raw_weekday_times)
            if weekday_error:
                return jsonify({"error": weekday_error}), 400
            normalized_weekday_times = normalized_weekday_times or {}

        # 可选日期范围：前端传 YYYY-MM-DD 字符串，后端解析为本地日期（Asia/Shanghai）
        raw_date_start = (data.get("date_start") or "").strip() or None
        raw_date_end = (data.get("date_end") or "").strip() or None
        date_start = None
        date_end = None
        try:
            if raw_date_start:
                # 视为本地日期 00:00:00
                date_start = datetime.strptime(raw_date_start, "%Y-%m-%d")
            if raw_date_end:
                # 视为本地日期 23:59:59，便于包含整天
                date_end = datetime.strptime(raw_date_end, "%Y-%m-%d") + timedelta(
                    hours=23, minutes=59, seconds=59
                )
        except ValueError:
            return jsonify({"error": "日期格式错误，应为 YYYY-MM-DD"}), 400

        if not lat or not lng or not isinstance(times, list):
            return jsonify({"error": "经纬度(coord/lat,lng) 为必填，times 必须是数组"}), 400

        # 统一为 HH:MM:SS 并剔除非法时间点，避免创建出永远不会执行的任务
        normalized_times = parse_times(times)
        # 每天默认时间可以为空，只要按星期配置里还有会执行的星期即可
        if not has_any_execution(
            normalized_times, normalized_weekday_times or {}
        ):
            return jsonify(
                {"error": "没有有效的时间点：请至少设置一个每天或按星期执行的时间"}
            ), 400

        # 本次要签到的班级：前端传 class_ids；未传时默认识别到的全部班级
        # （老页面只会传一个班级的旧逻辑，等价于这里的默认值）
        available_classes = {
            c["class_id"]: c for c in (state.classes or []) if c.get("class_id")
        }
        raw_class_ids = data.get("class_ids", "__missing__")
        if raw_class_ids == "__missing__" or raw_class_ids is None:
            target_class_ids = list(available_classes.keys())
            if not target_class_ids and state.class_id:
                target_class_ids = [state.class_id]
        else:
            if not isinstance(raw_class_ids, list):
                return jsonify({"error": "class_ids 必须是数组"}), 400
            target_class_ids = []
            for raw_id in raw_class_ids:
                cid = str(raw_id).strip()
                if cid and cid not in target_class_ids:
                    target_class_ids.append(cid)
            if not target_class_ids:
                return jsonify({"error": "请至少选择一个班级"}), 400
            if available_classes:
                unknown = [cid for cid in target_class_ids if cid not in available_classes]
                if unknown:
                    return (
                        jsonify(
                            {
                                "error": "class_ids 中包含本次扫码未识别的班级："
                                + "、".join(unknown)
                            }
                        ),
                        400,
                    )

        if not target_class_ids:
            return jsonify({"error": "未获取到班级信息，请重新扫码登录"}), 400

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        payloads = []
        any_created = False
        with SessionLocal() as db:
            # 一个班级一条任务：多班级账号提交一次，会为每个选中的班级各建/更新一条，
            # 之后各自独立调度、独立推送通知。
            for class_id in target_class_ids:
                class_name = (available_classes.get(class_id) or {}).get("class_name")

                # 同一账号（姓名 + 班级）只保留一个任务：
                # 重复提交时更新已有任务，而不是不断新建，否则会重复签到，
                # 且旧任务会带着过期 cookie 继续执行。
                existing_tasks = (
                    db.query(Task)
                    .filter(Task.name == state.user_name, Task.class_id == class_id)
                    .order_by(Task.created_at.desc(), Task.id.desc())
                    .all()
                )

                if existing_tasks:
                    task = existing_tasks[0]

                    # 清理历史遗留的重复任务（连同其调度 job 与日志）
                    for duplicated in existing_tasks[1:]:
                        unregister_task_jobs(app, duplicated.id)
                        db.delete(duplicated)

                    task.cookie = state.cookie
                    if class_name:
                        task.class_name = class_name
                    task.lat = str(lat)
                    task.lng = str(lng)
                    task.acc = str(acc)
                    task.pushplus_token = pushplus_token
                    task.times = json.dumps(normalized_times, ensure_ascii=False)
                    if weekday_times_provided:
                        task.weekday_times = json.dumps(
                            normalized_weekday_times, ensure_ascii=False
                        )
                    task.date_start = date_start
                    task.date_end = date_end
                    task.enabled = True
                    db.commit()
                    db.refresh(task)

                    # 时间点可能变化，先清空旧 job 再重建
                    unregister_task_jobs(app, task.id)
                    register_task_jobs(app, task)
                else:
                    task = Task(
                        name=state.user_name,
                        class_id=class_id,
                        class_name=class_name,
                        cookie=state.cookie,
                        lat=str(lat),
                        lng=str(lng),
                        acc=str(acc),
                        pushplus_token=pushplus_token,
                        times=json.dumps(normalized_times, ensure_ascii=False),
                        weekday_times=(
                            json.dumps(normalized_weekday_times, ensure_ascii=False)
                            if weekday_times_provided
                            else None
                        ),
                        enabled=True,
                        date_start=date_start,
                        date_end=date_end,
                    )
                    db.add(task)
                    db.commit()
                    db.refresh(task)

                    # 为新任务注册调度 job
                    register_task_jobs(app, task)
                    any_created = True

                payloads.append(
                    {
                        "id": task.id,
                        "name": task.name,
                        "class_id": task.class_id,
                        "class_name": task.class_name,
                        "lat": task.lat,
                        "lng": task.lng,
                        "acc": task.acc,
                        "pushplus_token": task.pushplus_token,
                        "times": json.loads(task.times or "[]"),
                        "weekday_times": dump_weekday_times(task.weekday_times),
                        "enabled": task.enabled,
                        "date_start": task.date_start.isoformat() if task.date_start else None,
                        "date_end": task.date_end.isoformat() if task.date_end else None,
                    }
                )

        # 顶层字段保留第一条任务的，兼容只认单个任务的旧前端；
        # 新前端读 tasks 拿到本次保存的全部班级。
        response_payload = dict(payloads[0])
        response_payload["tasks"] = payloads
        response_payload["class_ids"] = target_class_ids
        return jsonify(response_payload), 201 if any_created else 200

    @app.get("/api/tasks/<int:task_id>")
    def api_get_task(task_id: int):
        """
        返回任务详情（不包含 cookie）。
        """
        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                return jsonify({"error": "not found"}), 404

            return jsonify(
                {
                    "id": task.id,
                    "name": task.name,
                    "class_id": task.class_id,
                    "class_name": task.class_name,
                    "lat": task.lat,
                    "lng": task.lng,
                    "acc": task.acc,
                    "pushplus_token": task.pushplus_token,
                    "times": json.loads(task.times or "[]"),
                    "weekday_times": dump_weekday_times(task.weekday_times),
                    "enabled": task.enabled,
                    "date_start": task.date_start.isoformat() if task.date_start else None,
                    "date_end": task.date_end.isoformat() if task.date_end else None,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                }
            )

    @app.get("/api/tasks/<int:task_id>/logs")
    def api_get_task_logs(task_id: int):
        """
        查询指定任务的最近执行日志（按任务 ID）。

        Query 参数：
        - limit: 返回条数，默认 20，最大 100
        """
        try:
            limit = int(request.args.get("limit", "20"))
        except ValueError:
            limit = 20
        if limit <= 0:
            limit = 20
        if limit > 100:
            limit = 100

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                return jsonify({"error": "not found"}), 404

            logs = (
                db.query(TaskLog)
                .filter(TaskLog.task_id == task_id)
                .order_by(TaskLog.run_at.desc())
                .limit(limit)
                .all()
            )

    # ========== 阶段三扩展：基于扫码会话的用户配置与日志 ==========

            return jsonify(
                [
                    {
                        "id": log.id,
                        "run_at": log.run_at.isoformat() if log.run_at else None,
                        "status": log.status,
                        "message": log.message,
                    }
                    for log in logs
                ]
            )

    @app.get("/api/user/config")
    def api_get_user_config():
        """
        基于当前扫码会话（token 映射到 cookie）返回该账号最近一次任务配置，
        并判断本次扫码获取的 cookie 与数据库中记录的是否一致。

        Query 参数：
        - token: 扫码登录 token（必填）
        """
        token = request.args.get("token")
        if not token:
            return jsonify({"error": "missing token"}), 400

        state = qr_service.get_session_state(token)
        if not state or not state.cookie:
            return jsonify({"error": "invalid or expired token"}), 400

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            # 找出该账号（按 cookie）最近一次的任务配置；
            # 这里按 created_at 倒序取第一个。
            task = (
                db.query(Task)
                .filter(Task.cookie == state.cookie)
                .order_by(Task.created_at.desc())
                .first()
            )

            if not task:
                return jsonify(
                    {
                        "has_task": False,
                        "cookie_changed": False,
                        "config": None,
                    }
                )

            # 这里的 cookie_changed 主要用于「提示已更新」场景；
            # 当前实现里 Task.cookie 就是最近一次使用的 cookie，
            # 若你未来引入多次扫码并保留历史 cookie，可在此对比。
            cookie_changed = task.cookie != state.cookie

            return jsonify(
                {
                    "has_task": True,
                    "cookie_changed": cookie_changed,
                    "config": {
                        "id": task.id,
                        "name": task.name,
                        "class_id": task.class_id,
                        "class_name": task.class_name,
                        "lat": task.lat,
                        "lng": task.lng,
                        "acc": task.acc,
                        "pushplus_token": task.pushplus_token,
                        "times": json.loads(task.times or "[]"),
                        "weekday_times": dump_weekday_times(task.weekday_times),
                        "enabled": task.enabled,
                        "date_start": task.date_start.isoformat() if task.date_start else None,
                        "date_end": task.date_end.isoformat() if task.date_end else None,
                        "created_at": task.created_at.isoformat() if task.created_at else None,
                        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                    },
                }
            )

    @app.get("/api/user/logs")
    def api_get_user_logs():
        """
        基于当前扫码会话（token 映射到 cookie）查询该账号下所有任务的执行日志。

        Query 参数：
        - token: 扫码登录 token（必填）
        - limit: 返回条数，默认 20，最大 100
        """
        token = request.args.get("token")
        if not token:
            return jsonify({"error": "missing token"}), 400

        state = qr_service.get_session_state(token)
        if not state or not state.cookie:
            return jsonify({"error": "invalid or expired token"}), 400

        try:
            limit = int(request.args.get("limit", "20"))
        except ValueError:
            limit = 20
        if limit <= 0:
            limit = 20
        if limit > 100:
            limit = 100

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            # 找出该 cookie 对应的所有任务
            tasks = db.query(Task).filter(Task.cookie == state.cookie).all()
            if not tasks:
                return jsonify([])

            task_ids = [t.id for t in tasks]
            logs = (
                db.query(TaskLog, Task)
                .join(Task, TaskLog.task_id == Task.id)
                .filter(TaskLog.task_id.in_(task_ids))
                .order_by(TaskLog.run_at.desc())
                .limit(limit)
                .all()
            )

            return jsonify(
                [
                    {
                        "id": log.id,
                        "run_at": log.run_at.isoformat() if log.run_at else None,
                        "status": log.status,
                        "message": log.message,
                        "task": {
                            "id": task.id,
                            "name": task.name,
                            "class_id": task.class_id,
                            "class_name": task.class_name,
                            "lat": task.lat,
                            "lng": task.lng,
                        },
                    }
                    for log, task in logs
                ]
            )

    # ========== 阶段五：管理员后台与运维接口（后端部分） ==========

    def _hash_admin_key(key: str) -> str:
        """
        对管理员 Key 做简单哈希存储，避免明文落库。
        """
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _get_admin_key_hash(db):
        setting = db.query(Setting).filter(Setting.key == "admin_key_hash").first()
        return setting.value if setting else None

    def _require_admin(app):
        """
        管理员认证校验。

        当前部署需求：后台无需密码即可查看与管理所有任务，
        因此此函数直接放行，不再强制要求 admin token。

        如需重新开启鉴权，可恢复为从请求头 `X-Admin-Token` /
        查询参数 `admin_token` 中读取 token 并校验有效期。
        """
        return "public", None

    @app.post("/api/admin/init")
    def api_admin_init():
        """
        初始化管理员 Key：
        - 仅当数据库中不存在 admin_key_hash 时允许调用；
        - Body: { "key": "你的管理员密码" }。
        """
        SessionLocal = app.config["DB_SESSION_FACTORY"]
        data = request.get_json(silent=True) or {}
        key = (data.get("key") or "").strip()
        if not key:
            return jsonify({"error": "missing key"}), 400

        with SessionLocal() as db:
            existing_hash = _get_admin_key_hash(db)
            if existing_hash:
                return jsonify({"error": "admin key already initialized"}), 409

            setting = Setting(
                key="admin_key_hash",
                value=_hash_admin_key(key),
            )
            db.add(setting)
            db.commit()

        return jsonify({"status": "ok"})

    @app.post("/api/admin/login")
    def api_admin_login():
        """
        管理员登录：提交管理员 Key，返回一个短期 admin token。

        Body:
        - key: 管理员密码

        响应:
        - token: 管理员访问 token
        - expires_in: 过期时间（秒），默认 24 小时
        """
        SessionLocal = app.config["DB_SESSION_FACTORY"]
        data = request.get_json(silent=True) or {}
        key = (data.get("key") or "").strip()
        if not key:
            return jsonify({"error": "missing key"}), 400

        with SessionLocal() as db:
            key_hash = _get_admin_key_hash(db)
            if not key_hash:
                return jsonify({"error": "admin key not initialized"}), 400

            if _hash_admin_key(key) != key_hash:
                return jsonify({"error": "invalid admin key"}), 401

        # 生成 admin token，默认有效期 24 小时
        token = secrets.token_urlsafe(24)
        expires_in = 24 * 3600
        expire_at = datetime.utcnow() + timedelta(seconds=expires_in)
        app.config["ADMIN_TOKENS"][token] = expire_at

        return jsonify({"token": token, "expires_in": expires_in})

    @app.get("/api/admin/tasks")
    def api_admin_list_tasks():
        """
        管理员查看任务列表。

        认证：需要有效的 admin token（X-Admin-Token）。

        Query 参数（可选）：
        - name: 按姓名模糊搜索
        - class_id: 按班级标识模糊搜索
        - enabled: 1 / 0 过滤启用状态
        """
        _, error_resp = _require_admin(app)
        if error_resp:
            return error_resp

        name = (request.args.get("name") or "").strip()
        class_id = (request.args.get("class_id") or "").strip()
        enabled = request.args.get("enabled")

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            query = db.query(Task)
            if name:
                query = query.filter(Task.name.contains(name))
            if class_id:
                query = query.filter(Task.class_id.contains(class_id))
            if enabled in ("0", "1"):
                flag = enabled == "1"
                query = query.filter(Task.enabled.is_(flag))

            tasks = query.order_by(Task.id.desc()).all()

            return jsonify(
                [
                    {
                        "id": t.id,
                        "name": t.name,
                        "class_id": t.class_id,
                        "class_name": t.class_name,
                        "lat": t.lat,
                        "lng": t.lng,
                        "acc": t.acc,
                        "pushplus_token": t.pushplus_token,
                        "times": json.loads(t.times or "[]"),
                        "weekday_times": dump_weekday_times(t.weekday_times),
                        "enabled": t.enabled,
                        "date_start": t.date_start.isoformat() if t.date_start else None,
                        "date_end": t.date_end.isoformat() if t.date_end else None,
                        "created_at": t.created_at.isoformat() if t.created_at else None,
                        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                    }
                    for t in tasks
                ]
            )

    @app.patch("/api/admin/tasks/<int:task_id>")
    def api_admin_update_task(task_id: int):
        """
        管理员修改任务配置（位置、时间、通知 Key、启用状态等）。

        Body JSON：可选字段，按需更新
        - lat, lng, acc
        - pushplus_token
        - times: ["07:30:00","12:30:15"]（每天默认时间点）
        - weekday_times: {"1": ["07:30:00"], "7": []}（按星期覆盖，空数组表示该天不签到）
        - enabled: bool
        - date_start: "2026-03-01"（可选，任务开始日期）
        - date_end: "2026-03-31"（可选，任务结束日期）
        """
        _, error_resp = _require_admin(app)
        if error_resp:
            return error_resp

        data = request.get_json(silent=True) or {}

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                return jsonify({"error": "not found"}), 404

            if "lat" in data:
                task.lat = str(data.get("lat") or task.lat)
            if "lng" in data:
                task.lng = str(data.get("lng") or task.lng)
            if "acc" in data:
                task.acc = str(data.get("acc") or task.acc)
            if "pushplus_token" in data:
                task.pushplus_token = data.get("pushplus_token") or ""
            if "times" in data:
                times = data.get("times") or []
                if not isinstance(times, list):
                    return jsonify({"error": "times must be a list"}), 400
                # 统一为 HH:MM:SS，避免存进 "7:5" 这类解析不了的时间点
                normalized = parse_times(times)
                if times and not normalized:
                    return jsonify({"error": "times 中没有有效的时间点"}), 400
                task.times = json.dumps(normalized, ensure_ascii=False)
            if "weekday_times" in data:
                normalized_weekday, weekday_error = _normalize_weekday_times(
                    data.get("weekday_times")
                )
                if weekday_error:
                    return jsonify({"error": weekday_error}), 400
                task.weekday_times = json.dumps(
                    normalized_weekday or {}, ensure_ascii=False
                )
            if "enabled" in data:
                task.enabled = bool(data.get("enabled"))

            # 启用的任务必须至少有一个会执行的时间点（每天默认或按星期配置），
            # 否则等于挂着不执行。
            if task.enabled and not has_any_execution(task.times, task.weekday_times):
                return jsonify({"error": "启用状态下至少需要一个有效的时间点"}), 400
            # 日期范围更新：允许传入 YYYY-MM-DD 字符串或 null/空字符串
            raw_date_start = data.get("date_start", "__missing__")
            raw_date_end = data.get("date_end", "__missing__")
            try:
                if raw_date_start != "__missing__":
                    if raw_date_start:
                        task.date_start = datetime.strptime(raw_date_start, "%Y-%m-%d")
                    else:
                        task.date_start = None
                if raw_date_end != "__missing__":
                    if raw_date_end:
                        task.date_end = datetime.strptime(raw_date_end, "%Y-%m-%d") + timedelta(
                            hours=23, minutes=59, seconds=59
                        )
                    else:
                        task.date_end = None
            except ValueError:
                return jsonify({"error": "日期格式错误，应为 YYYY-MM-DD"}), 400

            db.commit()
            db.refresh(task)

            # 任何影响调度的改动（时间点、启用状态、日期范围）都整体重建 job：
            # register_task_jobs 只会新增/替换，必须先移除旧的，否则被删掉的时间点仍在执行。
            unregister_task_jobs(app, task.id)
            if task.enabled:
                register_task_jobs(app, task)

            new_times = json.loads(task.times or "[]")

            return jsonify(
                {
                    "id": task.id,
                    "name": task.name,
                    "class_id": task.class_id,
                    "class_name": task.class_name,
                    "lat": task.lat,
                    "lng": task.lng,
                    "acc": task.acc,
                    "pushplus_token": task.pushplus_token,
                    "times": new_times,
                    "weekday_times": dump_weekday_times(task.weekday_times),
                    "enabled": task.enabled,
                    "date_start": task.date_start.isoformat() if task.date_start else None,
                    "date_end": task.date_end.isoformat() if task.date_end else None,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                }
            )

    @app.get("/api/admin/logs")
    def api_admin_logs():
        """
        管理员查看执行日志。

        认证：需要 admin token。

        Query 参数：
        - task_id: 可选，若提供则仅查看该任务日志
        - limit: 返回条数，默认 50，最大 200
        """
        _, error_resp = _require_admin(app)
        if error_resp:
            return error_resp

        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            limit = 50
        if limit <= 0:
            limit = 50
        if limit > 200:
            limit = 200

        task_id = request.args.get("task_id")

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            query = db.query(TaskLog, Task).join(Task, TaskLog.task_id == Task.id)
            if task_id:
                try:
                    task_id_int = int(task_id)
                    query = query.filter(TaskLog.task_id == task_id_int)
                except ValueError:
                    return jsonify({"error": "task_id must be integer"}), 400

            logs = query.order_by(TaskLog.run_at.desc()).limit(limit).all()

            return jsonify(
                [
                    {
                        "id": log.id,
                        "run_at": log.run_at.isoformat() if log.run_at else None,
                        "status": log.status,
                        "message": log.message,
                        "task": {
                            "id": task.id,
                            "name": task.name,
                            "class_id": task.class_id,
                            "class_name": task.class_name,
                            "lat": task.lat,
                            "lng": task.lng,
                        },
                    }
                    for log, task in logs
                ]
            )

    @app.delete("/api/admin/tasks/<int:task_id>")
    def api_admin_delete_task(task_id: int):
        """
        管理员删除任务。

        - 认证：需要 admin token。
        - 同时会移除 APScheduler 中对应的调度任务；
        - 由于 Task.logs 关系设置了 cascade="all, delete-orphan"，
          删除任务会自动级联删除该任务的所有日志。
        """
        _, error_resp = _require_admin(app)
        if error_resp:
            return error_resp

        SessionLocal = app.config["DB_SESSION_FACTORY"]
        with SessionLocal() as db:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                return jsonify({"error": "not found"}), 404

            # 先移除调度任务，再删数据库记录
            unregister_task_jobs(app, task.id)
            db.delete(task)
            db.commit()

        return jsonify({"status": "ok"})

    return app
