from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from .db import Base

# 调度器统一使用 Asia/Shanghai，日志与时间戳也按本地时间存储，避免前端显示偏差 8 小时
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def now_local() -> datetime:
    """当前 Asia/Shanghai 本地时间（naive），供数据库默认值使用。"""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # 学生姓名
    class_id = Column(String(100), nullable=False)  # 班级 ID 或名称
    # 班级名称（如「计算机学院25级计科和软件」），只用于展示；
    # 一个账号可能加入多个班级，每个班级会各有一条任务。
    class_name = Column(String(200), nullable=True)
    cookie = Column(Text, nullable=False)  # 登录凭据，仅后端使用
    lat = Column(String(32), nullable=False)
    lng = Column(String(32), nullable=False)
    acc = Column(String(32), nullable=True)
    pushplus_token = Column(String(255), nullable=True)
    times = Column(Text, nullable=False)  # JSON 字符串，存每天执行的多个时间点（未单独配置的星期沿用这里）
    # JSON 字符串，按星期单独配置的执行时间点，形如 {"1": ["07:30:00"], "7": []}。
    # 键为 ISO 星期（1=周一 … 7=周日）；值为空数组表示该天不签到；未出现的星期沿用 times。
    weekday_times = Column(Text, nullable=True)
    # 可选的任务生效日期范围（本地日期，按 Asia/Shanghai 理解）；为空则表示不限制
    date_start = Column(DateTime, nullable=True)
    date_end = Column(DateTime, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_local)
    updated_at = Column(DateTime, default=now_local, onupdate=now_local)

    logs = relationship("TaskLog", back_populates="task", cascade="all, delete-orphan")


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    run_at = Column(DateTime, default=now_local, index=True)
    status = Column(String(32), nullable=False)  # success / error / skipped
    message = Column(Text, nullable=False)  # 存储整段可读日志文本

    task = relationship("Task", back_populates="logs")


class Setting(Base):
    """
    存储全局配置，例如管理员 Key 哈希等。
    """

    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    created_at = Column(DateTime, default=now_local)
    updated_at = Column(DateTime, default=now_local, onupdate=now_local)

