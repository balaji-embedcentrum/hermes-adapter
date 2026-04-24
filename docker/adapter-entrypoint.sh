#!/bin/bash
# Adapter image entrypoint — used when this image runs on top of
# nousresearch/hermes-agent. The upstream entrypoint runs `hermes ...`,
# which we don't want; instead we mirror upstream's HERMES_HOME bootstrap
# (so AIAgent finds the dirs it expects) and exec the adapter CLI.

set -e

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"

mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills}

if [ ! -f "$HERMES_HOME/.env" ] && [ -f "$INSTALL_DIR/.env.example" ]; then
    cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
fi
if [ ! -f "$HERMES_HOME/config.yaml" ] && [ -f "$INSTALL_DIR/cli-config.yaml.example" ]; then
    cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
fi
if [ ! -f "$HERMES_HOME/SOUL.md" ] && [ -f "$INSTALL_DIR/docker/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
fi

exec hermes-adapter "$@"
