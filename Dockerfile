FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git is required for all workspace git/* endpoints and gh for PR creation
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY hermes_adapter ./hermes_adapter

# Install with [a2a] extras by default so the A2A server works out of the box.
# Callers who only need the workspace API can override CMD and skip the extra.
RUN pip install '.[a2a]'

# Default: run both workspace API (:8766) and A2A server (:9000) in one process
ENV HERMES_ADAPTER_HOST=0.0.0.0 \
    HERMES_ADAPTER_PORT=8766 \
    A2A_HOST=0.0.0.0 \
    A2A_PORT=9000 \
    HERMES_WORKSPACE_DIR=/workspaces

EXPOSE 8766 9000

ENTRYPOINT ["hermes-adapter"]
CMD ["serve"]
