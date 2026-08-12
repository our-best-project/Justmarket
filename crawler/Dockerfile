# 正式 Scrapy 執行面。也被 spider_forge 當成沙盒容器用來實跑產出的候選 spider，
# 所以這個映像檔刻意不含專案套件（--no-install-project）：它只要跑得動 scrapy。
#
# build context 是 repo 根目錄，不是本目錄 —— 依賴唯一來源在根目錄的 uv.lock。
ARG UV_VERSION=0.12.1

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HOME=/tmp/home \
    XDG_CONFIG_HOME=/tmp/config \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    SPIDERFORGE_SANDBOX=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/runtime/.venv/bin:$PATH"

COPY --from=uv-bin /uv /usr/local/bin/uv
WORKDIR /runtime

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra crawler \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 crawler \
    && mkdir -p /tmp/home /tmp/config /output \
    && chown -R crawler:crawler /tmp/home /tmp/config /output /ms-playwright /runtime

COPY --chown=crawler:crawler crawler/scrapy.cfg .
COPY --chown=crawler:crawler crawler/news_crawler ./news_crawler

USER 10001:10001

ENTRYPOINT ["python", "-m", "scrapy"]
CMD ["list"]
