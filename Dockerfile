# The requirements and the wheel are the same on every platform: build them once,
# on the build host, with the versions and hashes from uv.lock.
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

FROM --platform=$BUILDPLATFORM python:3.14.9-alpine@sha256:dbeb11d16bb95c3a7e3c25c842cb9f20cbc8c18f8be0461a4a0dcc861ad386a0 AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

COPY README.md pyproject.toml uv.lock /app/
COPY fritzlogextractor /app/fritzlogextractor

RUN uv export --frozen --no-dev --no-emit-project --format requirements.txt \
        --output-file requirements.txt && \
    uv build --wheel

# Build the actual runner
FROM python:3.14.9-alpine@sha256:dbeb11d16bb95c3a7e3c25c842cb9f20cbc8c18f8be0461a4a0dcc861ad386a0

LABEL Name=fritz_logextractor
ENV PIP_NO_CACHE_DIR="true" \
    PIP_DISABLE_PIP_VERSION_CHECK="true"

WORKDIR /app

COPY --from=build /app/requirements.txt /app/dist/*.whl /tmp/install/

RUN pip install --require-hashes --requirement /tmp/install/requirements.txt && \
    pip install --no-deps /tmp/install/*.whl && \
    rm -r /tmp/install && \
    mkdir -p /etc/fritz /var/lib/fritzlogextractor && \
    chown 65534:65534 /var/lib/fritzlogextractor

# nobody, as in fritz_exporter; owns the state directory so a volume there stays writable
USER 65534:65534

VOLUME ["/var/lib/fritzlogextractor"]

ENTRYPOINT ["python", "-m", "fritzlogextractor"]
