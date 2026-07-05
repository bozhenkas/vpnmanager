#!/usr/bin/env python3
"""MCP stdio framing adapter for mempalace 3.5.0.

The packaged mempalace-mcp server currently speaks newline-delimited JSON-RPC
on stdio, while modern MCP clients use Content-Length framed messages.  This
adapter keeps the registered command MCP-compatible without patching the
remote venv package.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading


def _read_client_message() -> bytes | None:
    stream = sys.stdin.buffer
    first = stream.readline()
    if not first:
        return None

    if first.lstrip().startswith(b"{"):
        return first.rstrip(b"\r\n")

    headers: dict[str, str] = {}
    line = first
    while line not in (b"\r\n", b"\n", b""):
        if b":" in line:
            key, value = line.decode("ascii", errors="ignore").split(":", 1)
            headers[key.strip().lower()] = value.strip()
        line = stream.readline()

    if not line:
        return None
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return b""
    return stream.read(length)


def _write_client_message(payload: bytes) -> None:
    out = sys.stdout.buffer
    out.write(b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n")
    out.write(payload)
    out.flush()


def _forward_stderr(proc: subprocess.Popen[bytes]) -> None:
    assert proc.stderr is not None
    for chunk in iter(lambda: proc.stderr.read(4096), b""):
        if not chunk:
            break
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()


def _forward_server_stdout(proc: subprocess.Popen[bytes]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        payload = line.rstrip(b"\r\n")
        if payload:
            _write_client_message(payload)


def main() -> int:
    ssh_key = os.environ.get("GOIDA_MEMPALACE_SSH_KEY", os.path.expanduser("~/.ssh/id_rsa"))
    ssh_user = os.environ.get("GOIDA_MEMPALACE_SSH_USER", "bozhenkas")
    ssh_host = os.environ.get("GOIDA_MEMPALACE_SSH_HOST", "78.107.88.21")
    ssh_port = os.environ.get("GOIDA_MEMPALACE_SSH_PORT", "1722")
    remote = (
        'export MEMPALACE_BACKEND=qdrant; '
        'export MEMPALACE_QDRANT_URL=http://127.0.0.1:6333; '
        'export MEMPALACE_QDRANT_NAMESPACE=goida; '
        'export MEMPALACE_PALACE_PATH="$HOME/mempalace-stack/palace"; '
        'export HF_HOME="$HOME/mempalace-stack/hf-cache"; '
        'exec "$HOME/mempalace-stack/venv/bin/mempalace-mcp" --backend qdrant'
    )
    proc = subprocess.Popen(
        [
            "ssh",
            "-i",
            ssh_key,
            "-p",
            ssh_port,
            "-o",
            "BatchMode=yes",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            f"{ssh_user}@{ssh_host}",
            remote,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    threading.Thread(target=_forward_stderr, args=(proc,), daemon=True).start()
    threading.Thread(target=_forward_server_stdout, args=(proc,), daemon=True).start()

    assert proc.stdin is not None
    try:
        while True:
            payload = _read_client_message()
            if payload is None:
                break
            if not payload:
                continue
            proc.stdin.write(payload + b"\n")
            proc.stdin.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
    return proc.wait(timeout=5) if proc.poll() is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
