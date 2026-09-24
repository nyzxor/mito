# Hardened T1 sandbox (ADR-0006). Built by `mito dev sandbox-build`.
FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && useradd --uid 65532 --user-group --create-home mito \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
USER 65532:65532
