"""Local HTTP->SOCKS5 proxy bridge.

Runs per-instance HTTP proxy servers that forward traffic through
remote SOCKS5 proxies. Android connects to 10.0.2.2:<port> (host gateway).

Each instance gets a unique port: 18800 + index.
"""

import asyncio
import multiprocessing
import os
import signal
import socket
import struct
import subprocess
from urllib.parse import urlparse
from loguru import logger

# Track running bridge processes: {instance_name: {"proc": Process, "local_port": int, ...}}
_bridges: dict[str, dict] = {}
_BASE_BRIDGE_PORT = 18800


def _kill_port_holder(port: int) -> None:
    """Kill any process listening on the given port.

    Uses lsof to find PIDs bound to the port and sends SIGKILL.
    Needed when a previous bridge process became orphaned.
    """
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().split()
        for pid_str in pids:
            pid = int(pid_str)
            if pid == os.getpid():
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                logger.info("Killed orphaned process pid={} on port {}", pid, port)
            except ProcessLookupError:
                pass
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass


def _socks5_connect(
    socks_host: str,
    socks_port: int,
    target_host: str,
    target_port: int,
    username: str | None = None,
    password: str | None = None,
) -> socket.socket:
    """Connect through SOCKS5 proxy and return connected socket."""
    sock = socket.create_connection((socks_host, socks_port), timeout=15)

    # Greeting
    if username and password:
        sock.sendall(b"\x05\x02\x00\x02")  # support no-auth + user/pass
    else:
        sock.sendall(b"\x05\x01\x00")  # no-auth only

    resp = sock.recv(2)
    if resp[0] != 0x05:
        sock.close()
        raise ConnectionError("Not a SOCKS5 proxy")

    # Auth
    if resp[1] == 0x02 and username and password:
        user_bytes = username.encode()
        pass_bytes = password.encode()
        sock.sendall(
            b"\x01"
            + bytes([len(user_bytes)]) + user_bytes
            + bytes([len(pass_bytes)]) + pass_bytes
        )
        auth_resp = sock.recv(2)
        if auth_resp[1] != 0x00:
            sock.close()
            raise ConnectionError("SOCKS5 auth failed")
    elif resp[1] != 0x00:
        sock.close()
        raise ConnectionError(f"SOCKS5 unsupported auth method: {resp[1]}")

    # Connect request
    try:
        addr = socket.inet_aton(target_host)
        sock.sendall(b"\x05\x01\x00\x01" + addr + struct.pack(">H", target_port))
    except socket.error:
        # Domain name
        host_bytes = target_host.encode()
        sock.sendall(
            b"\x05\x01\x00\x03"
            + bytes([len(host_bytes)]) + host_bytes
            + struct.pack(">H", target_port)
        )

    resp = sock.recv(10)
    if resp[1] != 0x00:
        sock.close()
        raise ConnectionError(f"SOCKS5 connect failed: error code {resp[1]}")

    return sock


async def _handle_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    socks_host: str,
    socks_port: int,
    username: str | None,
    password: str | None,
    target_host: str,
    target_port: int,
) -> None:
    """Handle CONNECT tunnel."""
    try:
        loop = asyncio.get_event_loop()
        sock = await loop.run_in_executor(
            None, _socks5_connect,
            socks_host, socks_port, target_host, target_port, username, password,
        )
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        remote_reader, remote_writer = await asyncio.open_connection(sock=sock)
        await asyncio.gather(
            _relay(reader, remote_writer),
            _relay(remote_reader, writer),
        )
    except ConnectionError as e:
        logger.debug("CONNECT tunnel failed: {}", e)
        try:
            writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{e}".encode())
            await writer.drain()
        except (ConnectionError, OSError):
            pass
    finally:
        writer.close()


async def _handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    socks_host: str,
    socks_port: int,
    username: str | None,
    password: str | None,
    method: str,
    url: str,
    http_version: str,
    headers_raw: bytes,
) -> None:
    """Handle regular HTTP request (non-CONNECT)."""
    try:
        parsed = urlparse(url)
        target_host = parsed.hostname or ""
        target_port = parsed.port or 80

        loop = asyncio.get_event_loop()
        sock = await loop.run_in_executor(
            None, _socks5_connect,
            socks_host, socks_port, target_host, target_port, username, password,
        )

        remote_reader, remote_writer = await asyncio.open_connection(sock=sock)

        # Rebuild request with relative path
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        request_line = f"{method} {path} {http_version}\r\n"
        remote_writer.write(request_line.encode() + headers_raw + b"\r\n")
        await remote_writer.drain()

        await _relay(remote_reader, writer)
    except (ConnectionError, OSError) as e:
        logger.debug("HTTP request failed: {}", e)
        try:
            writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{e}".encode())
            await writer.drain()
        except (ConnectionError, OSError):
            pass
    finally:
        writer.close()


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Relay data between two streams."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            writer.close()
        except (ConnectionError, OSError):
            pass


async def _proxy_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    socks_host: str,
    socks_port: int,
    username: str | None,
    password: str | None,
) -> None:
    """Handle incoming HTTP proxy request."""
    try:
        first_line = await asyncio.wait_for(reader.readline(), timeout=30)
        if not first_line:
            writer.close()
            return

        parts = first_line.decode().strip().split()
        if len(parts) < 3:
            writer.close()
            return

        method, url, http_version = parts[0], parts[1], parts[2]

        # Read headers
        headers_raw = b""
        while True:
            line = await reader.readline()
            if line == b"\r\n" or not line:
                break
            headers_raw += line

        if method == "CONNECT":
            host_port = url.split(":")
            target_host = host_port[0]
            target_port = int(host_port[1]) if len(host_port) > 1 else 443
            await _handle_connect(
                reader, writer, socks_host, socks_port,
                username, password, target_host, target_port,
            )
        else:
            await _handle_request(
                reader, writer, socks_host, socks_port,
                username, password, method, url, http_version, headers_raw,
            )
    except (asyncio.TimeoutError, ConnectionError, OSError):
        try:
            writer.close()
        except (ConnectionError, OSError):
            pass


def _run_bridge(
    local_port: int,
    socks_host: str,
    socks_port: int,
    username: str | None,
    password: str | None,
) -> None:
    """Run bridge in a separate process."""

    async def _serve() -> None:
        handler = lambda r, w: _proxy_handler(r, w, socks_host, socks_port, username, password)
        server = await asyncio.start_server(handler, "0.0.0.0", local_port)
        async with server:
            await server.serve_forever()

    asyncio.run(_serve())


def _get_assigned_ports() -> set[int]:
    """Get all bridge ports currently assigned in _bridges dict."""
    return {b["local_port"] for b in _bridges.values()}


def start_bridge(
    instance_name: str,
    socks_host: str,
    socks_port: int,
    socks_user: str | None = None,
    socks_pass: str | None = None,
    local_port: int | None = None,
) -> int:
    """Start a proxy bridge for an instance.

    Args:
        instance_name: BlueStacks instance name.
        socks_host: SOCKS5 proxy host.
        socks_port: SOCKS5 proxy port.
        socks_user: SOCKS5 auth username.
        socks_pass: SOCKS5 auth password.
        local_port: Force specific local port. Auto-assigned if None.

    Returns:
        The local HTTP proxy port.
    """
    # Always stop existing bridge for this instance first
    stop_bridge(instance_name)

    if local_port is None:
        used_ports = _get_assigned_ports()
        local_port = _BASE_BRIDGE_PORT
        while local_port in used_ports:
            local_port += 1

    # Kill any orphaned process holding this port
    _kill_port_holder(local_port)

    proc = multiprocessing.Process(
        target=_run_bridge,
        args=(local_port, socks_host, socks_port, socks_user, socks_pass),
        daemon=True,
    )
    proc.start()

    _bridges[instance_name] = {
        "proc": proc,
        "local_port": local_port,
        "socks_host": socks_host,
        "socks_port": socks_port,
    }

    logger.info(
        "Bridge started for '{}': 0.0.0.0:{} -> socks5://{}:{} (pid={})",
        instance_name, local_port, socks_host, socks_port, proc.pid,
    )
    return local_port


def stop_bridge(instance_name: str) -> None:
    """Stop a running bridge for the instance.

    Also kills the process by port if it became orphaned (e.g. dead multiprocessing.Process
    but the OS process still holds the port).
    """
    bridge = _bridges.pop(instance_name, None)
    if bridge is None:
        return

    port = bridge["local_port"]
    proc = bridge["proc"]

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=3)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        logger.info("Bridge stopped for '{}' (port {})", instance_name, port)
    else:
        # Process already dead but port might still be held
        logger.debug("Bridge process for '{}' already dead, cleaning port {}", instance_name, port)
        _kill_port_holder(port)


def stop_all_bridges() -> None:
    """Stop all running bridges."""
    for name in list(_bridges.keys()):
        stop_bridge(name)


def get_bridge_status(instance_name: str) -> dict | None:
    """Get bridge status for an instance."""
    bridge = _bridges.get(instance_name)
    if not bridge:
        return None
    return {
        "instance": instance_name,
        "local_port": bridge["local_port"],
        "running": bridge["proc"].is_alive(),
        "pid": bridge["proc"].pid,
    }


def list_bridges() -> list[dict]:
    """List all tracked bridges."""
    return [
        {
            "instance": name,
            "local_port": b["local_port"],
            "running": b["proc"].is_alive(),
            "pid": b["proc"].pid,
        }
        for name, b in _bridges.items()
    ]
