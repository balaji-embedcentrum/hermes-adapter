"""Fleet orchestration — per-session bind-mount management for the
multi-tenant agent pool.

The endpoints in this package assume:
  * the adapter has Docker socket access (/var/run/docker.sock mounted)
  * the adapter process has docker CLI available
  * the fleet was bootstrapped by scripts/install-fleet.sh so
    $FLEET_ROOT/docker-compose.yml + $FLEET_ROOT/agents/<name> exist

Contract (see hermes_adapter.fleet.routes for full details):

  POST /fleet/claim     body {agent, user}   — bind-mount user's workspace
                                                into the agent container,
                                                force-recreate, wait for
                                                health, return.

  POST /fleet/unclaim   body {agent}         — remove the user-specific
                                                override, force-recreate
                                                with an empty placeholder
                                                so the agent sees nothing.

  GET  /fleet/status                         — list agents + current user
                                                + healthy flag per agent.

Kernel-level isolation: each agent container only mounts one user's
directory at a time. No shared /workspaces, no symlinks that tools can
escape, no "search the whole fleet" leak. The tradeoff is a ~2-5s
container restart on every claim.
"""
