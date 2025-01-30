ARG BASE_IMAGE=python:3.11-slim

FROM $BASE_IMAGE AS base-image

ARG HTTP_ASGI_APPLICATION_PATH=
ARG ADDITIONAL_PACKAGES=
ARG GITHUB_TOKEN=

RUN apt-get update \
  && apt-get install -y git wget ffmpeg \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /root/src/

COPY ./pyproject.toml ./uv.lock ./

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1

RUN PDM_BUILD_SCM_VERSION=0 uv sync --frozen --no-install-project --no-dev --no-editable

RUN echo "Additional packages: $(echo "$ADDITIONAL_PACKAGES" | sed "s/\$GITHUB_TOKEN/$GITHUB_TOKEN/g")"
RUN [ -n "$ADDITIONAL_PACKAGES" ] && \
    echo "$ADDITIONAL_PACKAGES" | sed "s/\$GITHUB_TOKEN/$GITHUB_TOKEN/g" > additional-packages.txt && \
    uv pip install -r additional-packages.txt

COPY ./app/src/ /root/src
COPY ./app/envs/prod/entrypoint.sh ./app/envs/prod/gunicorn.conf.py ./app/envs/prod/celery-entrypoint.sh ./app/envs/prod/prometheus-cleanup.sh /root/src/

ENV PATH="/root/src/.venv/bin:$PATH"

RUN python3 -m compileall -b -f -q /root/
RUN ENV=prod ENV_FILL_MISSING_VALUES=1 SECRET_KEY=dummy uv run --no-sync python3 manage.py collectstatic --no-input --clear

EXPOSE 8000

ENV HTTP_ASGI_APPLICATION_PATH=$HTTP_ASGI_APPLICATION_PATH
CMD ["./entrypoint.sh"]
