"""ADB management for BlueStacks instances."""

import asyncio
from loguru import logger

from config import BS_ADB, SYSTEM_ADB


def _get_adb() -> str:
    """Get path to ADB binary."""
    if BS_ADB.exists():
        return str(BS_ADB)
    if SYSTEM_ADB.exists():
        return str(SYSTEM_ADB)
    raise FileNotFoundError("ADB not found at {} or {}".format(BS_ADB, SYSTEM_ADB))


async def adb_cmd(port: int, *args: str, timeout: int = 30) -> str:
    """Execute ADB command on instance by port.

    Args:
        port: ADB port of the target instance.
        args: ADB command arguments.
        timeout: Max seconds to wait for command.

    Returns:
        Stdout output stripped.

    Raises:
        RuntimeError: If ADB command returns non-zero exit code.
    """
    adb = _get_adb()
    cmd = [adb, "-s", f"127.0.0.1:{port}", *args]
    logger.debug("ADB cmd: {}", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    result = stdout.decode().strip()
    if proc.returncode != 0:
        err = stderr.decode().strip()
        logger.error("ADB error (port {}): {}", port, err)
        raise RuntimeError(f"ADB error: {err}")
    return result


async def adb_shell(port: int, command: str, timeout: int = 30) -> str:
    """Execute shell command via ADB."""
    return await adb_cmd(port, "shell", command, timeout=timeout)


async def connect(port: int) -> bool:
    """Connect to ADB instance."""
    adb = _get_adb()
    proc = await asyncio.create_subprocess_exec(
        adb, "connect", f"127.0.0.1:{port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    result = stdout.decode().strip()
    ok = "connected" in result.lower()
    logger.info("ADB connect 127.0.0.1:{} -> {}", port, "OK" if ok else result)
    return ok


async def disconnect(port: int) -> None:
    """Disconnect ADB instance."""
    adb = _get_adb()
    proc = await asyncio.create_subprocess_exec(
        adb, "disconnect", f"127.0.0.1:{port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    logger.debug("ADB disconnect 127.0.0.1:{}", port)


async def set_prop(port: int, prop: str, value: str) -> None:
    """Set system property via ADB shell."""
    await adb_shell(port, f"setprop {prop} {value}")


async def apply_build_props(port: int, props: dict[str, str]) -> None:
    """Apply multiple build properties."""
    for prop, value in props.items():
        try:
            await set_prop(port, prop, value)
            logger.debug("Set prop {} = {} on port {}", prop, value, port)
        except RuntimeError as e:
            logger.warning("Failed to set {} on port {}: {}", prop, port, e)


async def set_android_id(port: int, android_id: str) -> None:
    """Set Android ID via settings."""
    await adb_shell(port, f"settings put secure android_id {android_id}")
    logger.info("Set android_id={} on port {}", android_id, port)


async def clear_proxy(port: int) -> None:
    """Remove HTTP proxy settings from Android."""
    await adb_shell(port, "settings put global http_proxy :0")
    logger.info("Proxy cleared on port {}", port)


async def install_apk(port: int, apk_path: str) -> None:
    """Install APK on instance."""
    await adb_cmd(port, "install", "-r", apk_path, timeout=120)
    logger.info("Installed {} on port {}", apk_path, port)


async def get_device_info(port: int) -> dict[str, str]:
    """Get current device info from instance."""
    props = [
        "ro.product.brand", "ro.product.model", "ro.product.device",
        "ro.product.manufacturer", "ro.build.display.id",
    ]
    info: dict[str, str] = {}
    for prop in props:
        try:
            info[prop] = await adb_shell(port, f"getprop {prop}")
        except RuntimeError:
            info[prop] = "unknown"

    try:
        info["android_id"] = await adb_shell(port, "settings get secure android_id")
    except RuntimeError:
        info["android_id"] = "unknown"

    return info
