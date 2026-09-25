FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI, headless (`claude -p --output-format json`): used by
# app/gateway/providers.py::ClaudeAccountProvider for model_access "claude_account" (mode B,
# the owner's own Claude subscription). Needs CLAUDE_CODE_OAUTH_TOKEN at runtime — see .env.example.
RUN curl -fsSL https://claude.ai/install.sh | bash -s stable
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY registry ./registry
RUN pip install --no-cache-dir -e ".[anthropic]"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
