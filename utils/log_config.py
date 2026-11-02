"""
统一日志配置（整个项目共用）。

设计目标：
- 一个入口完成配置：控制台 + 滚动文件（app.log / error.log）；
- 文件按大小滚动，避免长期运行把磁盘写满；
- 每条日志带请求 ID、线程名，方便把一次 HTTP 请求或一次定时任务的
  多行日志串起来排查；
- 统一脱敏工具，避免 cookie / token 等敏感信息落到日志文件。

环境变量：
- LOG_LEVEL         日志级别，默认 INFO（DEBUG / INFO / WARNING / ERROR）
- LOG_DIR           日志目录，默认项目根目录下的 logs/
- LOG_TO_FILE       是否写文件，默认 1
- LOG_TO_CONSOLE    是否输出到控制台，默认 1
- LOG_MAX_BYTES     单个日志文件大小上限，默认 10MB
- LOG_BACKUP_COUNT  保留的历史日志文件数量，默认 5
- LOG_APP_FILE      全量日志文件名，默认 app.log
- LOG_ERROR_FILE    WARNING 及以上日志文件名，默认 error.log
- LOG_FORMAT        自定义日志格式
- LOG_HTTP_DEBUG    设为 1 时打开 urllib3 的 DEBUG（会包含完整请求 URL，
                    其中可能带 pushplus token，默认关闭）
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union
from zoneinfo import ZoneInfo

# utils/log_config.py -> utils -> 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"

DEFAULT_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(threadName)s | "
    "%(request_id)s | %(name)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 与调度器保持同一时区：即使容器默认是 UTC，日志时间也是北京时间，
# 方便和 task_logs 里的时间直接对照。
LOG_TIMEZONE = ZoneInfo("Asia/Shanghai")

# 标记本模块添加的 handler，重复调用 setup_logging 时只清理自己加的，不影响宿主
_HANDLER_MARK = "_bjmf_managed"
_CONFIGURED = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _resolve_level(level: Optional[Union[int, str]]) -> int:
    """把 LOG_LEVEL / 传入值解析为 logging 级别，非法值回退 INFO。"""
    if isinstance(level, int):
        return level
    raw = (level or os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    value = logging.getLevelName(raw)
    return value if isinstance(value, int) else logging.INFO


def get_log_dir() -> Path:
    """当前日志目录（便于启动日志里打印出来）。"""
    env_dir = os.getenv("LOG_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return DEFAULT_LOG_DIR


def mask_secret(value, visible: int = 4) -> str:
    """
    脱敏工具：用于 cookie / token / 密码等敏感值。

    只保留首尾少量字符，例如 pushplus token 会变成 "abcd***wxyz"；
    太短的值整体打码，避免仍能猜出原文。
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if len(text) <= visible * 2:
        return "*" * len(text)
    return f"{text[:visible]}***{text[-visible:]}"


def mask_cookie(cookie) -> str:
    """cookie 比普通 token 更长且含账号凭据，统一只保留很短的前缀。"""
    return mask_secret(cookie, visible=6)


def _current_request_id() -> str:
    """在 Flask 请求上下文中返回 g.request_id，否则返回 "-"。"""
    try:
        from flask import g, has_request_context

        if has_request_context():
            return getattr(g, "request_id", "-") or "-"
    except Exception:
        pass
    return "-"


class RequestContextFilter(logging.Filter):
    """
    给每条日志补上 request_id 字段。

    定时任务、后台线程等没有请求上下文的日志会显示 "-"，
    有请求上下文的日志则与 HTTP 响应头 X-Request-ID 一致，便于串联。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = _current_request_id()
        return True


class ShanghaiFormatter(logging.Formatter):
    """固定按 Asia/Shanghai 输出时间，避免容器 UTC 时区导致日志时间偏差。"""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created, LOG_TIMEZONE)
        return dt.strftime(datefmt or DATE_FORMAT)


def _build_formatter() -> logging.Formatter:
    return ShanghaiFormatter(
        os.getenv("LOG_FORMAT") or DEFAULT_LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )


def _add_managed_handler(root: logging.Logger, handler: logging.Handler) -> None:
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)


def _remove_managed_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def _configure_third_party_loggers(level: int) -> None:
    """压低第三方库噪音，并避免敏感内容通过它们落盘。"""
    # SQLAlchemy 的 engine 日志会打印 SQL 参数（可能包含 cookie），永远不要开
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    # Werkzeug 自带访问日志会和我们自己的请求日志重复，只保留告警以上
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    # APScheduler 的 INFO 日志（新增/移除 job）对排查调度问题很有用
    apscheduler_level = max(level, logging.INFO)
    logging.getLogger("apscheduler").setLevel(apscheduler_level)
    logging.getLogger("apscheduler.executors.default").setLevel(apscheduler_level)

    # HTTP 库默认不输出 DEBUG：urllib3 的 DEBUG 会打印完整 URL，
    # pushplus 的 token 就在 URL 里，打开后会明文落盘。
    if not _env_bool("LOG_HTTP_DEBUG", False):
        http_level = max(level, logging.WARNING)
        logging.getLogger("urllib3").setLevel(http_level)
        logging.getLogger("requests").setLevel(http_level)


def setup_logging(
    level: Optional[Union[int, str]] = None,
    log_dir: Optional[Union[str, Path]] = None,
    force: bool = False,
) -> logging.Logger:
    """
    初始化全局日志。

    幂等：重复调用不会重复添加 handler；传入 force=True 会先移除本模块
    之前添加的 handler，再按当前环境变量重新配置（便于测试或热更新）。
    """
    global _CONFIGURED

    root = logging.getLogger()
    if _CONFIGURED and not force:
        return root

    _remove_managed_handlers(root)

    resolved_level = _resolve_level(level)
    root.setLevel(resolved_level)

    formatter = _build_formatter()

    # 控制台：Docker / 前台运行时直接看得到
    if _env_bool("LOG_TO_CONSOLE", True):
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(resolved_level)
        console.setFormatter(formatter)
        _add_managed_handler(root, console)

    # 文件：长期运行与事后排查
    if _env_bool("LOG_TO_FILE", True):
        target_dir = Path(log_dir).expanduser() if log_dir else get_log_dir()
        max_bytes = _env_int("LOG_MAX_BYTES", 10 * 1024 * 1024)
        backup_count = _env_int("LOG_BACKUP_COUNT", 5)

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            app_file = target_dir / (os.getenv("LOG_APP_FILE") or "app.log")
            error_file = target_dir / (os.getenv("LOG_ERROR_FILE") or "error.log")

            app_handler = RotatingFileHandler(
                app_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            app_handler.setLevel(resolved_level)
            app_handler.setFormatter(formatter)
            _add_managed_handler(root, app_handler)

            # 单独一份 WARNING 及以上的日志，排查异常时不用再翻全量日志
            error_handler = RotatingFileHandler(
                error_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            error_handler.setLevel(max(resolved_level, logging.WARNING))
            error_handler.setFormatter(formatter)
            _add_managed_handler(root, error_handler)
        except OSError as exc:
            # 只读文件系统等场景：退回到仅控制台，不能让服务起不来
            sys.stderr.write(
                f"[logging] 无法创建日志目录/文件 {target_dir}: {exc}，将只输出到控制台\n"
            )

    _configure_third_party_loggers(resolved_level)
    _CONFIGURED = True

    logging.getLogger(__name__).debug(
        "日志系统已初始化: level=%s dir=%s",
        logging.getLevelName(resolved_level),
        get_log_dir() if _env_bool("LOG_TO_FILE", True) else "(仅控制台)",
    )
    return root