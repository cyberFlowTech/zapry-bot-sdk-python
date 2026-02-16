"""MCP Client — Transport layer (HTTP, Stdio, InProcess)."""

from __future__ import annotations

import asyncio
import logging
import urllib.request
import urllib.error
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger("zapry_agents_sdk.mcp.transport")

_MAX_ERROR_BODY = 128 * 1024  # 128KB


# ──────────────────────────────────────────────
# Transport Protocol
# ──────────────────────────────────────────────


@runtime_checkable
class MCPTransport(Protocol):
    """Low-level transport interface — request-response semantics."""

    async def start(self) -> None:
        ...

    async def call(self, payload: bytes) -> bytes:
        ...

    async def close(self) -> None:
        ...


# ──────────────────────────────────────────────
# MCPTransportError
# ──────────────────────────────────────────────


class MCPTransportError(Exception):
    """Wraps HTTP non-2xx responses with status code and body preview."""

    def __init__(self, status_code: int, body_preview: str = "") -> None:
        self.status_code = status_code
        self.body_preview = body_preview
        super().__init__(f"mcp: http {status_code}: {body_preview}")

    @property
    def is_retryable(self) -> bool:
        return self.status_code >= 500 or self.status_code == 429


# ──────────────────────────────────────────────
# HTTPTransport
# ──────────────────────────────────────────────


class HTTPTransport:
    """MCPTransport over HTTP POST (zero external dependencies).

    Uses ``urllib.request`` in ``asyncio.to_thread`` to avoid blocking the
    event loop while keeping the dependency footprint at zero.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    async def start(self) -> None:
        pass

    async def call(self, payload: bytes) -> bytes:
        return await asyncio.to_thread(self._sync_call, payload)

    def _sync_call(self, payload: bytes) -> bytes:
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json", **self.headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = ""
            try:
                raw = e.read(_MAX_ERROR_BODY)
                body = raw.decode("utf-8", errors="replace")
                if len(body) > 512:
                    body = body[:512] + "..."
            except Exception:
                pass
            raise MCPTransportError(e.code, body) from e

    async def close(self) -> None:
        pass


# ──────────────────────────────────────────────
# StdioTransport
# ──────────────────────────────────────────────


class StdioTransport:
    """MCPTransport via child process stdin/stdout.

    Architecture:
    - A long-lived reader task reads stdout lines into an ``asyncio.Queue``.
    - ``call()`` writes to stdin then reads from the queue.
    - stderr is consumed and logged (never parsed as JSON).
    """

    def __init__(
        self,
        command: str,
        args: Optional[list] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self.timeout = timeout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._lines: asyncio.Queue[bytes] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._closed = False

    async def start(self) -> None:
        import os

        env = dict(os.environ)
        if self.env:
            env.update(self.env)

        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            stripped = line.strip()
            if stripped:
                await self._lines.put(stripped)

    async def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            logger.info("[MCP:stdio:%s] stderr: %s", self.command, line.decode("utf-8", errors="replace").strip())

    async def call(self, payload: bytes) -> bytes:
        if self._closed or self._process is None:
            raise RuntimeError("mcp: stdio transport not started")

        if self._process.returncode is not None:
            raise RuntimeError("mcp: stdio process exited")

        assert self._process.stdin
        self._process.stdin.write(payload + b"\n")
        await self._process.stdin.drain()

        try:
            line = await asyncio.wait_for(
                self._lines.get(),
                timeout=self.timeout,
            )
            return line
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError("mcp: stdio read timeout")

    async def close(self) -> None:
        self._closed = True
        if self._process and self._process.stdin:
            try:
                self._process.stdin.close()
            except Exception:
                pass

        if self._process:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


# ──────────────────────────────────────────────
# InProcessTransport (for testing)
# ──────────────────────────────────────────────


class InProcessTransport:
    """MCPTransport that delegates to a handler function directly.

    Used for deterministic testing without external processes or network.
    """

    def __init__(self, handler: Callable[[bytes], bytes]) -> None:
        self.handler = handler

    async def start(self) -> None:
        pass

    async def call(self, payload: bytes) -> bytes:
        return self.handler(payload)

    async def close(self) -> None:
        pass
