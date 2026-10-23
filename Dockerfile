ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

# psycopg needs libpq at runtime when not using the [binary] wheel extras alone on some platforms.
# curl is only needed to fetch the Claude Code CLI installer below.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Extra root certificates (docker/ca/README.md): for a machine whose antivirus or proxy inspects HTTPS.
COPY docker/ca/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Claude Code CLI, headless (`claude -p --output-format json`): used by
# app/gateway/providers.py::ClaudeAccountProvider for model_access "claude_account" (mode B,
# the owner's own Claude subscription). Needs CLAUDE_CODE_OAUTH_TOKEN at runtime — see .env.example.
RUN curl -fsSL https://claude.ai/install.sh | bash -s stable
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY registry ./registry
COPY web ./web
COPY docker/entrypoint.sh /entrypoint.sh
# A checkout on Windows may have CRLF line endings; a shell script that has them fails to start.
RUN tr -d '\015' < /entrypoint.sh > /entrypoint.lf && mv /entrypoint.lf /entrypoint.sh && chmod +x /entrypoint.sh

RUN pip install --no-cache-dir -e ".[postgres,anthropic]"

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
