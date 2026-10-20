FROM python:3.11-slim AS litestream
ARG LITESTREAM_VERSION=0.5.12
ADD https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.tar.gz /tmp/ls.tgz
RUN tar -C /usr/local/bin -xzf /tmp/ls.tgz litestream

FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/entrypoint.sh /entrypoint.sh
RUN useradd -m app && mkdir /data && chown app /data && chmod +x /entrypoint.sh
USER app
ENV PATH="/app/.venv/bin:$PATH" PUNCTUAL_DB_PATH=/data/punctual.db
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8080'))"
ENTRYPOINT ["/entrypoint.sh"]
