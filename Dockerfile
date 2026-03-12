FROM python:3.9-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制应用代码
COPY anchor/ anchor/
COPY sources.yaml .

EXPOSE 8765

CMD ["anchor", "serve", "--host", "0.0.0.0", "--port", "8765"]
