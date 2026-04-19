"""Process supervisor for the workspace adapter + N hermes-a2a agents.

``hermes-adapter up`` reads ``agents.yaml`` and does three things:
  1. Starts the workspace aiohttp app inline (in this process).
  2. Spawns one ``hermes-a2a`` subprocess per configured agent, each with
     its own ``HERMES_HOME``, ``AGENT_NAME``, and ``A2A_PORT``.
  3. Streams every subprocess's stdout/stderr with a ``[name]`` prefix
     and forwards Ctrl-C to graceful shutdown.

``hermes-adapter down`` reads the pidfile written by `up` and sends SIGTERM
to every child. ``hermes-adapter status`` prints what's alive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .manifest import DEFAULT_LOG_DIR, DEFAULT_RUN_DIR, Manifest


logger = logging.getLogger(__name__)
PIDFILE = DEFAULT_RUN_DIR / "supervisor.pid"
STATEFILE = DEFAULT_RUN_DIR / "supervisor.json"


@dataclass
class ProcState:
    name: str
    pid: int
    port: int
    started_at: float


class Supervisor:
    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._children: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: list[asyncio.Task] = []
        self._shutting_down = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def run(self, detach: bool = False) -> None:
        if detach:
            if not self._daemonize():
                return
        DEFAULT_RUN_DIR.mkdir(parents=True, exist_ok=True)
        DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        PIDFILE.write_text(str(os.getpid()))
        try:
            asyncio.run(self._run_async())
        finally:
            self._cleanup_state()

    async def _run_async(self) -> None:
        # 1. Start workspace API in-process.
        from aiohttp import web
        from .workspace.app import build_app

        os.environ["HERMES_ADAPTER_CORS_ORIGINS"] = ",".join(self.manifest.adapter.cors_origins)
        os.environ["HERMES_WORKSPACE_DIR"] = self.manifest.adapter.workspace_dir
        Path(self.manifest.adapter.workspace_dir).mkdir(parents=True, exist_ok=True)

        ws_app = build_app()
        runner = web.AppRunner(ws_app)
        await runner.setup()
        site = web.TCPSite(runner, host=self.manifest.adapter.host, port=self.manifest.adapter.port)
        await site.start()
        logger.info(
            "workspace API: http://%s:%d  (workspace=%s)",
            self.manifest.adapter.host,
            self.manifest.adapter.port,
            self.manifest.adapter.workspace_dir,
        )

        # 2. Spawn one hermes-a2a per agent.
        for spec in self.manifest.agents:
            await self._spawn_agent(spec)

        self._write_state()

        # 3. Wait for signals.
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()

        def _trigger_stop(*_: object) -> None:
            logger.info("received signal — stopping...")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _trigger_stop)
            except NotImplementedError:
                signal.signal(sig, lambda *_: _trigger_stop())

        # Also stop if any child exits unexpectedly.
        async def _watchdog() -> None:
            while not stop_event.is_set():
                for name, proc in list(self._children.items()):
                    if proc.returncode is not None and not self._shutting_down:
                        logger.error("agent '%s' exited with rc=%s — stopping stack", name, proc.returncode)
                        stop_event.set()
                        return
                await asyncio.sleep(1)

        self._tasks.append(asyncio.create_task(_watchdog()))

        await stop_event.wait()
        self._shutting_down = True
        await self._shutdown()
        await runner.cleanup()

    async def _spawn_agent(self, spec) -> None:
        hermes_home = spec.resolved_home()
        if not hermes_home.exists():
            logger.warning("agent '%s' has no HERMES_HOME at %s — skipping", spec.name, hermes_home)
            return

        env = os.environ.copy()
        env["HERMES_HOME"] = str(hermes_home)
        env["AGENT_NAME"] = spec.name
        env["AGENT_DESCRIPTION"] = spec.description or spec.name
        env["A2A_HOST"] = "127.0.0.1"
        env["A2A_PORT"] = str(spec.port)
        if self.manifest.a2a_key:
            env["A2A_KEY"] = self.manifest.a2a_key

        # hermes-a2a console script lives in the same venv as hermes-adapter
        hermes_a2a = shutil.which("hermes-a2a") or "hermes-a2a"
        log_path = DEFAULT_LOG_DIR / f"{spec.name}.log"
        log_fh = open(log_path, "ab", buffering=0)

        proc = await asyncio.create_subprocess_exec(
            hermes_a2a,
            env=env,
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._children[spec.name] = proc
        logger.info(
            "agent '%s' started (pid=%d, port=%d, log=%s)",
            spec.name,
            proc.pid,
            spec.port,
            log_path,
        )

    async def _shutdown(self) -> None:
        for name, proc in self._children.items():
            if proc.returncode is None:
                logger.info("stopping '%s' (pid=%d)...", name, proc.pid)
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass

        deadline = time.monotonic() + 10
        for name, proc in self._children.items():
            remaining = max(0, deadline - time.monotonic())
            try:
                await asyncio.wait_for(proc.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                logger.warning("'%s' did not stop in time — sending SIGKILL", name)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

        for t in self._tasks:
            t.cancel()

    def _write_state(self) -> None:
        state = {
            "pid": os.getpid(),
            "adapter": {
                "host": self.manifest.adapter.host,
                "port": self.manifest.adapter.port,
            },
            "agents": [
                asdict(
                    ProcState(
                        name=name,
                        pid=proc.pid,
                        port=self._port_for(name),
                        started_at=time.time(),
                    )
                )
                for name, proc in self._children.items()
            ],
        }
        STATEFILE.write_text(json.dumps(state, indent=2))

    def _cleanup_state(self) -> None:
        for p in (PIDFILE, STATEFILE):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def _port_for(self, name: str) -> int:
        spec = self.manifest.find(name)
        return spec.port if spec else 0

    def _daemonize(self) -> bool:
        """Fork off so `hermes-adapter up --detach` returns quickly.

        Returns True in the child (which continues into _run_async) and
        False in the parent (which should return from run()).
        """
        if sys.platform.startswith("win"):
            logger.warning("--detach not supported on Windows; running in foreground")
            return True
        pid = os.fork()
        if pid > 0:
            print(f"✓ supervisor detached (pid={pid})")
            return False
        os.setsid()
        # Redirect std streams in the daemon
        DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = open(DEFAULT_LOG_DIR / "supervisor.log", "ab", buffering=0)
        os.dup2(out.fileno(), sys.stdout.fileno())
        os.dup2(out.fileno(), sys.stderr.fileno())
        return True

    # ------------------------------------------------------------------
    # class methods used by `down` / `status`
    # ------------------------------------------------------------------

    @classmethod
    def stop_running(cls) -> bool:
        if not PIDFILE.exists():
            print("no supervisor running", file=sys.stderr)
            return False
        try:
            pid = int(PIDFILE.read_text().strip())
        except ValueError:
            print("pidfile corrupt — cleaning up", file=sys.stderr)
            PIDFILE.unlink(missing_ok=True)
            return False

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            print(f"supervisor pid {pid} not running — cleaning up stale pidfile")
            PIDFILE.unlink(missing_ok=True)
            STATEFILE.unlink(missing_ok=True)
            return True

        # Wait up to 15s
        for _ in range(150):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                print(f"✓ supervisor (pid={pid}) stopped")
                return True
            time.sleep(0.1)

        print(f"supervisor (pid={pid}) did not stop — escalating to SIGKILL", file=sys.stderr)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True

    @classmethod
    def print_status(cls) -> None:
        if not STATEFILE.exists():
            print("not running")
            return
        try:
            state = json.loads(STATEFILE.read_text())
        except json.JSONDecodeError:
            print("statefile corrupt — run `hermes-adapter down` to clean up")
            return

        sup_pid = state.get("pid")
        try:
            os.kill(sup_pid, 0)
            sup_alive = True
        except (ProcessLookupError, TypeError):
            sup_alive = False

        print(f"supervisor pid={sup_pid}  {'alive' if sup_alive else 'STALE'}")
        adapter = state.get("adapter") or {}
        print(f"  adapter:  http://{adapter.get('host')}:{adapter.get('port')}")
        for a in state.get("agents") or []:
            try:
                os.kill(a["pid"], 0)
                live = "alive"
            except ProcessLookupError:
                live = "dead"
            print(f"  agent {a['name']:<12} pid={a['pid']:<6} port={a['port']:<6} {live}")
