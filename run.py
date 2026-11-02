import logging
import os
import sys

# 兼容 `run.py` 启动：把项目根目录加入 sys.path，
# 保证 utils 包（日志、签到逻辑）可以被导入。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 尽早初始化日志，保证启动阶段（读取端口、创建应用）的错误也能落盘
from utils.log_config import get_log_dir, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

from app import create_app  # noqa: E402  （必须在 setup_logging 之后导入）


def _load_port_from_config() -> int:
    """
    从配置文件 / 环境变量中加载端口号。

    优先级：
    1. 环境变量 WEB_SIGNIN_PORT
    2. 默认 8000
    """
    default_port = 8000

    # 1. 环境变量优先，便于 Docker / 部署平台覆盖
    env_port = os.getenv("WEB_SIGNIN_PORT")
    if env_port:
        try:
            port = int(env_port)
            if 1 <= port <= 65535:
                logger.info("端口来源: 环境变量 WEB_SIGNIN_PORT=%s", port)
                return port
            logger.warning("WEB_SIGNIN_PORT=%r 超出范围，忽略", env_port)
        except ValueError:
            logger.warning("WEB_SIGNIN_PORT=%r 不是整数，忽略", env_port)

    logger.info("端口来源: 默认值 %s", default_port)
    return default_port


try:
    app = create_app()
except Exception:
    # 初始化失败（数据库不可写等）必须留下完整堆栈，否则容器里只能看到退出码
    logger.exception("应用初始化失败")
    raise


if __name__ == "__main__":
    # 运行时根据配置文件 / 环境变量决定端口
    port = _load_port_from_config()
    logger.info(
        "Web 服务启动: http://0.0.0.0:%s 日志目录=%s 级别=%s",
        port,
        get_log_dir(),
        logging.getLevelName(logging.getLogger().level),
    )
    try:
        app.run(host="0.0.0.0", port=port, debug=False)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，Web 服务退出")
    except Exception:
        logger.exception("Web 服务异常退出")
        raise
    else:
        logger.info("Web 服务已停止")