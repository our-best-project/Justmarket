# 兩個映像檔，一份依賴來源（pyproject.toml + uv.lock）。
#
# api    → 只讀 FastAPI。不含 torch／scrapy，維持小體積。
# worker → Prefect runner：爬蟲＋向量化＋LLM＋評分整條線。
#
# 依賴一律走 `uv sync --frozen`：鎖檔說了算，build 不會偷偷解出不同版本。
# 先只裝依賴、後複製原始碼，是為了讓「改程式不改依賴」時能命中 layer 快取。

ARG PREFECT_VERSION=3.8.0
ARG UV_VERSION=0.12.1

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin


# ─────────────────────────────────────────────────────────── api
FROM python:3.12-slim-bookworm AS api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Taipei \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/justmarket/.venv/bin:$PATH"

COPY --from=uv-bin /uv /usr/local/bin/uv
WORKDIR /opt/justmarket

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra api

# 整個 src/ 一起複製：wheel metadata 涵蓋 eventsignal 與 spider_forge 兩個套件，
# 少一個 hatch 就建不起來。api 不會 import spider_forge。
COPY src ./src
RUN uv sync --frozen --no-dev --extra api \
    && useradd --create-home --uid 10001 justmarket \
    && chown -R justmarket:justmarket /opt/justmarket

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "eventsignal", "api"]


# ──────────────────────────────────────────────────────── worker
FROM prefecthq/prefect:${PREFECT_VERSION}-python3.12 AS worker

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Taipei \
    HOME=/home/justmarket \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/justmarket/.venv/bin:$PATH"

COPY --from=uv-bin /uv /usr/local/bin/uv
USER root
WORKDIR /opt/justmarket

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra worker --extra crawler

COPY src ./src
COPY crawler ./crawler
# crawler_legacy/base.py 與 ingestion.py 都從 repo 根算 data/ticker_stoplist.json
COPY data/ticker_stoplist.json ./data/ticker_stoplist.json
RUN uv sync --frozen --no-dev --extra worker --extra crawler \
    && useradd --create-home --uid 10001 justmarket \
    && mkdir -p src/eventsignal/embedding/out \
               src/eventsignal/clustering/out \
               /home/justmarket/.cache/huggingface \
    && chown -R justmarket:justmarket /opt/justmarket /home/justmarket

USER 10001:10001
CMD ["python", "-m", "eventsignal", "serve-flows"]
