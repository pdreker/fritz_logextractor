# Build .whl in a separate build container
FROM python:3.14.9-alpine@sha256:dbeb11d16bb95c3a7e3c25c842cb9f20cbc8c18f8be0461a4a0dcc861ad386a0 AS build
WORKDIR /app

RUN pip install --upgrade pip build

COPY README.md pyproject.toml /app/
COPY fritzlogextractor /app/fritzlogextractor

RUN python -m build --wheel

# Build the actual runner
FROM python:3.14.9-alpine@sha256:dbeb11d16bb95c3a7e3c25c842cb9f20cbc8c18f8be0461a4a0dcc861ad386a0

LABEL Name=fritz_logextractor
ENV PIP_NO_CACHE_DIR="true"

WORKDIR /app

COPY --from=build /app/dist/*.whl /

RUN pip install /fritz_logextractor-*.whl && \
    rm /fritz_logextractor-*.whl && \
    mkdir -p /etc/fritz /var/lib/fritzlogextractor

VOLUME ["/var/lib/fritzlogextractor"]

ENTRYPOINT ["python", "-m", "fritzlogextractor"]
