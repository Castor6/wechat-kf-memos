FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
RUN useradd --uid 10001 --create-home bridge && mkdir /data && chown bridge:bridge /data
USER bridge
ENV DATA_DIR=/data
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "wechat_kf_memos.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
