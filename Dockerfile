FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /srv

# Зависимости отдельным слоем — правки кода не тянут за собой пересборку.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY . .

# Права на файлы теряются при переносе с Windows — выставляем явно.
RUN chmod +x ./deploy/entrypoint.sh

CMD ["./deploy/entrypoint.sh"]
