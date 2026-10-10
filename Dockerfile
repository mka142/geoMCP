# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.23-python3.12-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package geomcp-server

FROM python:3.12-slim-trixie
# libexpat1: GDAL inside the rasterio wheel needs it (WorldCover, GUGiK NMT)
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 geomcp && mkdir /data && chown geomcp /data
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    GEOMCP_DATA_DIR=/data \
    GEOMCP_HOST=0.0.0.0 \
    GEOMCP_PORT=8000 \
    PYTHONUNBUFFERED=1
USER geomcp
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["geomcp", "serve", "--http"]
