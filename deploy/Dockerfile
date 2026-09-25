# Handbrake and runtime image (DESIGN §14). Non-root. No secrets in the image.
FROM python:3.12-slim-bookworm

RUN useradd --uid 65532 --user-group --create-home mito \
    && mkdir -p /var/lib/mito \
    && chown -R 65532:65532 /var/lib/mito

WORKDIR /app
COPY pyproject.toml README.md ./
COPY handbrake handbrake
COPY mito mito
COPY policy policy
COPY config config
COPY prompts prompts
COPY skills skills
COPY playbooks playbooks
COPY evals/safety evals/safety

RUN pip install --no-cache-dir .

USER 65532:65532
ENV MITO_HOME=/var/lib/mito
ENV MITO_DEV_MODE=0
ENTRYPOINT ["mito"]
CMD ["up", "--no-runtime"]
