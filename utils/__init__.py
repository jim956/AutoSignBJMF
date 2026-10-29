"""
BJMF 工具包
包含用户信息获取、消息通知和签到功能模块。
这些模块由 web_signin 的调度器复用，是签到逻辑的唯一实现。
"""

from .user_info import (
    get_user_and_class_info,
    get_user_and_classes,
    parse_classes,
    get_current_time,
)
from .notification import send_to_pushplus
from .attendance import Task

__all__ = [
    'get_user_and_class_info',
    'get_user_and_classes',
    'parse_classes',
    'get_current_time',
    'send_to_pushplus',
    'Task',
]