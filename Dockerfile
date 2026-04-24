FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git — workspace git/* endpoints
# gh — PR creation
# docker CLI + compose plugin — fleet control plane shells out to
#   `docker compose` against the host's mounted socket when
#   FLEET_ROOT is set (see hermes_adapter.fleet).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         git curl ca-certificates gnupg lsb-release \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
         -o /etc/apt/keyrings/docker.asc \
    && chmod go+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
         > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         gh docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY hermes_adapter ./hermes_adapter

# Install hermes-agent so the unified gateway's AIAgent bridge works
# (both OpenAI-compat and A2A handlers import ``run_agent.AIAgent``).
# Without this, ``hermes-adapter-gateway`` loads fine but every chat
# request fails with "hermes-agent is not importable".
RUN pip install hermes-agent \
    && pip install '.[a2a]'

# Default: workspace API on :8766, unified gateway on :9001 (OpenAI + A2A +
# workspace routes on one port — used by per-agent fleet containers).
ENV HERMES_ADAPTER_HOST=0.0.0.0 \
    HERMES_ADAPTER_PORT=8766 \
    A2A_HOST=0.0.0.0 \
    A2A_PORT=9001 \
    HERMES_WORKSPACE_DIR=/workspaces

EXPOSE 8766 9001

ENTRYPOINT ["hermes-adapter"]
CMD ["serve"]
