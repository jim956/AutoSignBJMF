FROM python:3.12-slim

# 基础环境设置
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# 将整个仓库拷贝进容器：签到逻辑复用了根目录下的 utils 包
WORKDIR /app
COPY . .

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 安装 Web 版所需依赖
RUN pip install --upgrade pip && pip install -r ./requirements.txt

# 默认暴露端口
EXPOSE 8000

# 容器启动命令
CMD ["python", "./run.py"]

