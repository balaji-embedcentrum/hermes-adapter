"""Async subprocess runner used by all workspace routes."""

from __future__ import annotations

import asyncio


async def run(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    """Run *cmd* in *cwd*; return (returncode, stdout, stderr) with text decoding."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )
