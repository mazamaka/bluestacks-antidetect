"""Proxy management for BlueStacks instances."""

import asyncio
import json
import re
import socket
import time
from pathlib import Path
from loguru import logger
import httpx

from adb_manager import connect, adb_shell
from bs_conf import parse_conf

PROFILES_FILE = Path(__file__).parent / "profiles.json"

# Regex to match proxy-like strings: host:port or user:pass@host:port variants
_PROXY_LINE_RE = re.compile(
    r"^"
    r"(?:(?:socks[45]|https?)://)?"  # optional protocol
    r"(?:[^@\s]+@)?"                 # optional user:pass@
    r"[\w.\-]+"                      # host (ip or domain)
    r":\d{2,5}"                      # :port
    r"(?::[\w.\-]+:\S+)?"            # optional :user:pass suffix
    r"$"
)


def load_profiles() -> dict:
    """Load instance profiles (proxy assignments)."""
    if PROFILES_FILE.exists():
        try:
            return json.loads(PROFILES_FILE.read_text())
        except json.JSONDecodeError:
            logger.warning("Corrupted profiles.json, returning empty")
            return {}
    return {}


def save_profiles(profiles: dict) -> None:
    """Save instance profiles."""
    PROFILES_FILE.write_text(json.dumps(profiles, indent=2, ensure_ascii=False))
    logger.debug("Saved {} profiles to {}", len(profiles), PROFILES_FILE)


def parse_proxy_line(line: str) -> str | None:
    """Validate that a line looks like a proxy string.

    Filters out names, dates, messenger artifacts, etc.
    Returns cleaned proxy string or None if not a proxy.
    """
    line = line.strip()
    if not line:
        return None
    # Must contain at least one colon and a port-like number
    if ":" not in line:
        return None
    if _PROXY_LINE_RE.match(line):
        return line
    return None


def filter_proxy_lines(text: str) -> list[str]:
    """Extract valid proxy strings from text (may contain junk lines).

    Args:
        text: Raw text, possibly pasted from messengers with names/dates.

    Returns:
        List of cleaned proxy strings.
    """
    results: list[str] = []
    for line in text.splitlines():
        proxy = parse_proxy_line(line)
        if proxy:
            results.append(proxy)
    logger.debug("Filtered {} proxy lines from {} total lines", len(results), len(text.splitlines()))
    return results


async def assign_proxy(instance_name: str, proxy_string: str) -> None:
    """Assign SOCKS5 proxy to instance.

    Starts a local HTTP->SOCKS5 bridge and sets Android HTTP proxy
    via ADB to 10.0.2.2:<bridge_port> (host gateway from guest).

    Args:
        instance_name: BlueStacks instance name.
        proxy_string: Proxy in format socks5://user:pass@host:port
            or user:pass@host:port or host:port:user:pass.

    Raises:
        ValueError: If proxy format invalid or proxy not working.
    """
    from proxy_bridge import start_bridge

    host, port, username, password = _parse_proxy(proxy_string)

    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{instance_name}.adb_port", "0"))
    if not adb_port:
        raise ValueError(f"Instance '{instance_name}' not found or no ADB port")

    # 1. Validate proxy
    check = await validate_proxy(proxy_string)
    if not check["valid"]:
        raise ValueError(f"Proxy not working: {check['error']}")

    # 2. Start local HTTP->SOCKS5 bridge
    bridge_port = start_bridge(instance_name, host, port, username, password)

    # 3. Save to profiles
    profiles = load_profiles()
    profiles[instance_name] = {
        "proxy": proxy_string,
        "proxy_host": host,
        "proxy_port": port,
        "proxy_user": username,
        "proxy_pass": password,
        "bridge_port": bridge_port,
        "proxy_ip": check.get("ip", ""),
        "proxy_country": check.get("country", ""),
        "proxy_city": check.get("city", ""),
    }
    save_profiles(profiles)

    # 4. Apply HTTP proxy via ADB (Android uses 10.0.2.2 to reach host)
    applied_adb = False
    try:
        if await connect(adb_port):
            await adb_shell(adb_port, f"settings put global http_proxy 10.0.2.2:{bridge_port}")
            applied_adb = True
            logger.info("HTTP proxy set on Android: 10.0.2.2:{}", bridge_port)
    except RuntimeError as e:
        logger.warning("ADB apply skipped for '{}': {}", instance_name, e)

    logger.info(
        "Proxy assigned to '{}': {}:{} bridge=:{} (ADB: {})",
        instance_name, host, port, bridge_port,
        "applied" if applied_adb else "saved, apply on start",
    )


async def remove_proxy(instance_name: str) -> None:
    """Remove proxy from instance."""
    from proxy_bridge import stop_bridge

    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{instance_name}.adb_port", "0"))
    if not adb_port:
        raise ValueError(f"Instance '{instance_name}' not found")

    stop_bridge(instance_name)

    try:
        if await connect(adb_port):
            await adb_shell(adb_port, "settings put global http_proxy :0")
    except RuntimeError:
        pass

    profiles = load_profiles()
    profiles.pop(instance_name, None)
    save_profiles(profiles)
    logger.info("Proxy removed from '{}'", instance_name)


async def check_ip(instance_name: str) -> str:
    """Check current external IP of instance via ADB."""
    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{instance_name}.adb_port", "0"))
    if not adb_port:
        raise ValueError(f"Instance '{instance_name}' not found")

    if not await connect(adb_port):
        raise ConnectionError(f"Cannot connect to '{instance_name}'")

    ip = await adb_shell(
        adb_port,
        "curl -s https://api.ipify.org 2>/dev/null || wget -qO- https://api.ipify.org 2>/dev/null",
    )
    logger.info("Instance '{}' external IP: {}", instance_name, ip)
    return ip


async def validate_proxy(proxy_string: str) -> dict:
    """Validate and check proxy connectivity.

    Returns:
        Dict with keys: valid, proxy, ip, country, city, latency_ms, error.
    """
    result: dict = {
        "valid": False,
        "proxy": proxy_string,
        "ip": None,
        "country": None,
        "city": None,
        "latency_ms": None,
        "error": None,
    }

    try:
        host, port, username, password = _parse_proxy(proxy_string)
        result["parsed_host"] = host
        result["parsed_port"] = port
    except ValueError as e:
        result["error"] = f"Invalid format: {e}"
        return result

    # DNS resolve
    try:
        socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        result["error"] = f"DNS resolve failed: {host}"
        return result

    # SOCKS5 connectivity + IP check
    proxy_url = f"socks5://{username}:{password}@{host}:{port}" if username else f"socks5://{host}:{port}"

    try:
        start = time.monotonic()
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(15.0),
        ) as client:
            resp = await client.get("https://ipinfo.io/json")
            latency = round((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            result["valid"] = True
            result["ip"] = data.get("ip")
            result["country"] = data.get("country")
            result["city"] = data.get("city")
            result["org"] = data.get("org", "")
            result["latency_ms"] = latency
            logger.info(
                "Proxy OK: {} -> {} ({}, {}) {}ms",
                proxy_string, result["ip"], result["country"], result["city"], latency,
            )
        else:
            result["error"] = f"HTTP {resp.status_code}"
    except httpx.ProxyError as e:
        result["error"] = f"SOCKS5 auth/connect failed: {e}"
        logger.warning("Proxy FAIL: {} -> {}", proxy_string, e)
    except httpx.ConnectTimeout:
        result["error"] = "Connection timeout (15s)"
    except httpx.ReadTimeout:
        result["error"] = "Read timeout (15s)"
    except httpx.ConnectError as e:
        result["error"] = f"Connect error: {e}"
        logger.warning("Proxy FAIL: {} -> {}", proxy_string, e)

    return result


async def batch_validate_proxies(proxy_strings: list[str]) -> list[dict]:
    """Validate multiple proxies concurrently."""
    tasks = [validate_proxy(p) for p in proxy_strings]
    return await asyncio.gather(*tasks)


def _parse_proxy(proxy_string: str) -> tuple[str, int, str | None, str | None]:
    """Parse proxy string into (host, port, username, password).

    Supported formats:
        socks5://user:pass@host:port
        user:pass@host:port
        host:port:user:pass
        host:port

    Raises:
        ValueError: If format is unrecognized.
    """
    # Strip protocol prefix
    for prefix in ("socks5://", "socks4://", "http://", "https://"):
        if proxy_string.startswith(prefix):
            proxy_string = proxy_string[len(prefix):]
            break

    # Format: user:pass@host:port
    if "@" in proxy_string:
        auth, hostport = proxy_string.rsplit("@", 1)
        user, password = auth.split(":", 1)
        host, port_str = hostport.rsplit(":", 1)
        return host, int(port_str), user, password

    # Format: host:port:user:pass or host:port
    parts = proxy_string.split(":")
    if len(parts) == 4:
        return parts[0], int(parts[1]), parts[2], parts[3]
    if len(parts) == 2:
        return parts[0], int(parts[1]), None, None

    raise ValueError("Invalid proxy format. Use: user:pass@host:port or host:port:user:pass")
