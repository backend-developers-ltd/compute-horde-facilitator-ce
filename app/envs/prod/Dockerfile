ARG BASE_IMAGE=python:3.11-slim

FROM $BASE_IMAGE AS base-image
LABEL builder=true
ARG ADDITIONAL_PACKAGES=
ARG GITHUB_TOKEN=

WORKDIR /root/src/

RUN pip3 install --no-cache-dir pdm==2.19.3

COPY ./pyproject.toml ./pdm.lock ./
RUN pdm lock --check

RUN apt-get update && apt-get install -y git

RUN pdm config python.use_venv False && \
    pdm sync --prod --group :all --no-isolation

RUN apt-get update && apt-get install -y git

RUN echo "Additional packages: $(echo "$ADDITIONAL_PACKAGES" | sed "s/\$GITHUB_TOKEN/$GITHUB_TOKEN/g")"
RUN [ -n "$ADDITIONAL_PACKAGES" ] && \
    echo "$ADDITIONAL_PACKAGES" | sed "s/\$GITHUB_TOKEN/$GITHUB_TOKEN/g" > additional-packages.txt && \
    pip install -r additional-packages.txt



RUN mkdir -p /opt/ && mv __pypackages__/3.11/ /opt/pypackages/
ENV PATH=/opt/pypackages/bin:$PATH
ENV PYTHONPATH=/opt/pypackages/lib:$PYTHONPATH

COPY ./app/src/ /root/src
COPY ./app/envs/prod/entrypoint.sh ./app/envs/prod/gunicorn.conf.py ./app/envs/prod/celery-entrypoint.sh ./app/envs/prod/prometheus-cleanup.sh /root/src/

RUN python3 -m compileall -b -f -q /root/
RUN ENV=prod ENV_FILL_MISSING_VALUES=1 SECRET_KEY=dummy python3 manage.py collectstatic --no-input --clear


FROM $BASE_IMAGE AS secondary-image
LABEL builder=false
ARG HTTP_ASGI_APPLICATION_PATH=
ARG ADDITIONAL_PACKAGES=
ARG GITHUB_TOKEN=

RUN apt-get update && apt-get install -y git

RUN echo "Additional packages: $(echo "$ADDITIONAL_PACKAGES" | sed "s/\$GITHUB_TOKEN/$GITHUB_TOKEN/g")"
RUN [ -n "$ADDITIONAL_PACKAGES" ] && \
    echo "$ADDITIONAL_PACKAGES" | sed "s/\$GITHUB_TOKEN/$GITHUB_TOKEN/g" > additional-packages.txt && \
    pip install -r additional-packages.txt

RUN apt-get update \
  && apt-get install -y wget ffmpeg \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /root/src/
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/pypackages/bin:$PATH
ENV PYTHONPATH=/opt/pypackages/lib:$PYTHONPATH

COPY --from=base-image /root/src/ /root/src/
COPY --from=base-image /opt/pypackages/ /opt/pypackages/



EXPOSE 8000

ENV HTTP_ASGI_APPLICATION_PATH=$HTTP_ASGI_APPLICATION_PATH
CMD ["./entrypoint.sh"]
