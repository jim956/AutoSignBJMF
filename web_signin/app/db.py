from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "web_signin.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


class Base(DeclarativeBase):
    pass


def get_engine():
    url = f"sqlite:///{DB_PATH}"
    engine = create_engine(url, echo=False, connect_args={"check_same_thread": False})
    return engine


def get_session_factory(engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _migrate_task_columns(engine):
    """
    为已存在的老数据库补齐后加的列。

    create_all 只会新建缺失的表，不会修改已有表结构，所以新增字段需要在这里
    显式 ALTER TABLE，否则老库启动后查询 tasks 会因缺列直接报错。
    """
    inspector = inspect(engine)
    if "tasks" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("tasks")}
    additions = {
        # 按星期单独配置的签到时间点（JSON 字符串），为空表示全部沿用 times
        "weekday_times": "TEXT",
        # 班级名称，仅用于展示（多班级账号每个班级一条任务）
        "class_name": "VARCHAR(200)",
    }
    with engine.begin() as conn:
        for column, ddl_type in additions.items():
            if column not in existing:
                conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {column} {ddl_type}"))


def init_db():
    """
    初始化数据库引擎和会话工厂，并创建所有表。
    具体表结构在 models.py 中定义。
    """
    from . import models  # noqa: F401  # 导入以注册模型

    engine = get_engine()
    models.Base.metadata.create_all(bind=engine)
    _migrate_task_columns(engine)
    SessionLocal = get_session_factory(engine)
    return engine, SessionLocal

