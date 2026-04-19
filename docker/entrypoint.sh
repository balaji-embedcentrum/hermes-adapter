#!/usr/bin/env bash
# Thin entrypoint used when hermes-adapter runs outside a vanilla Python image
# (e.g. as a sidecar container in a larger stack with supervisord).
#
# Prefer plain `hermes-adapter serve` inside your own compose file — this
# script is only kept around for supervisord / systemd-style deployments.

set -euo pipefail

: "${HERMES_ADAPTER_HOST:=0.0.0.0}"
: "${HERMES_ADAPTER_PORT:=8766}"
: "${A2A_HOST:=0.0.0.0}"
: "${A2A_PORT:=9000}"
: "${HERMES_WORKSPACE_DIR:=/workspaces}"

mkdir -p "${HERMES_WORKSPACE_DIR}"

echo "[hermes-adapter] workspace=${HERMES_WORKSPACE_DIR}"
echo "[hermes-adapter] workspace api -> http://${HERMES_ADAPTER_HOST}:${HERMES_ADAPTER_PORT}"
echo "[hermes-adapter] a2a server    -> http://${A2A_HOST}:${A2A_PORT}"

exec hermes-adapter serve \
    --workspace-host "${HERMES_ADAPTER_HOST}" \
    --workspace-port "${HERMES_ADAPTER_PORT}" \
    --a2a-host "${A2A_HOST}" \
    --a2a-port "${A2A_PORT}" \
    "$@"
