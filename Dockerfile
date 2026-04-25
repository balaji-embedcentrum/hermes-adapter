# Base on the upstream hermes-agent image so ``run_agent.AIAgent`` is
# importable for the gateway's OpenAI-compat + A2A handlers. ``hermes-agent``
# is not published to PyPI, so this is the simplest reliable way to get it.
FROM nousresearch/hermes-agent:latest

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Add adapter-side system deps on top of the hermes-agent image:
#   curl                               — fetch keyrings for the gh + docker
#                                        repo setup below (NOT in the base
#                                        image despite git/python3 being
#                                        present)
#   python3-pip                        — base image strips pip after its
#                                        own install layer; we need it
#                                        back to install the adapter
#   gh                                 — workspace ``git pr`` endpoint
#   docker-ce-cli + docker-compose-plugin — fleet control plane shells out
#                                       to ``docker compose`` against the
#                                       host socket when FLEET_ROOT is set
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         curl ca-certificates gnupg lsb-release python3-pip \
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

# hermes-agent's image installs Python deps with --break-system-packages
# (debian:13 marks the system Python as PEP 668 externally-managed); we
# match so the adapter lands in the same site-packages where ``run_agent``
# already lives. Use ``python3 -m pip`` because the base image doesn't
# expose a ``pip`` shim on PATH.
RUN python3 -m pip install --break-system-packages '.[a2a]'

# Workspace API on :8766, unified gateway on :9001 (OpenAI + A2A + workspace
# routes on one port — used by per-agent fleet containers).
ENV HERMES_ADAPTER_HOST=0.0.0.0 \
    HERMES_ADAPTER_PORT=8766 \
    A2A_HOST=0.0.0.0 \
    A2A_PORT=9001 \
    HERMES_WORKSPACE_DIR=/workspaces

EXPOSE 8766 9001

# Bring our entrypoint over upstream's. Mirrors upstream's HERMES_HOME
# bootstrap (mkdir subdirs + seed defaults) so ``run_agent.AIAgent``
# finds the layout it expects, then execs the adapter CLI.
COPY docker/adapter-entrypoint.sh /usr/local/bin/adapter-entrypoint.sh
RUN chmod +x /usr/local/bin/adapter-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/adapter-entrypoint.sh"]
CMD ["serve"]
