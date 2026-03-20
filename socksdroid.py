"""SocksDroid UI automation helpers for BlueStacks instances."""

import asyncio
import re

from loguru import logger

from adb_manager import adb_shell


async def find_and_tap(port: int, text: str) -> bool:
    """Find UI element by text via uiautomator dump and tap its center.

    Args:
        port: ADB port of the target instance.
        text: Exact text of the UI element to tap.

    Returns:
        True if element found and tapped, False otherwise.
    """
    await adb_shell(port, "uiautomator dump /sdcard/ui.xml")
    xml = await adb_shell(port, "cat /sdcard/ui.xml")
    pattern = rf'text="{re.escape(text)}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    match = re.search(pattern, xml)
    if not match:
        pattern2 = rf'text="[^"]*{re.escape(text)}[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
        match = re.search(pattern2, xml)
    if not match:
        logger.warning("UI element '{}' not found on port {}", text, port)
        return False
    x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    await adb_shell(port, f"input tap {cx} {cy}")
    logger.debug("Tapped '{}' at ({}, {}) on port {}", text, cx, cy, port)
    return True


async def sd_set_field(port: int, label: str, value: str) -> None:
    """Tap a SocksDroid preference field, clear it and type a new value.

    Args:
        port: ADB port of the target instance.
        label: Label text of the preference field.
        value: New value to enter.
    """
    if not await find_and_tap(port, label):
        return
    await asyncio.sleep(0.8)
    await adb_shell(port, "uiautomator dump /sdcard/ui.xml")
    xml = await adb_shell(port, "cat /sdcard/ui.xml")
    edit_match = re.search(
        r'class="android.widget.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml
    )
    if edit_match:
        cx = (int(edit_match.group(1)) + int(edit_match.group(3))) // 2
        cy = (int(edit_match.group(2)) + int(edit_match.group(4))) // 2
        await adb_shell(port, f"input tap {cx} {cy}; input tap {cx} {cy}; input tap {cx} {cy}")
        await asyncio.sleep(0.3)
        await adb_shell(port, "input keyevent KEYCODE_DEL")
        await asyncio.sleep(0.2)
    await adb_shell(port, "input keyevent KEYCODE_MOVE_END")
    await adb_shell(port, "input keyevent " + " ".join(["67"] * 50))
    await asyncio.sleep(0.2)
    safe_value = value.replace("'", "'\\''")
    await adb_shell(port, f"input text '{safe_value}'")
    await asyncio.sleep(0.3)
    if not await find_and_tap(port, "OK"):
        await adb_shell(port, "input keyevent KEYCODE_ENTER")
    await asyncio.sleep(0.5)


async def configure_socksdroid_ui(
    port: int, host: str, socks_port: str, user: str, passwd: str
) -> None:
    """Full SocksDroid UI configuration via uiautomator.

    Args:
        port: ADB port of the target instance.
        host: SOCKS5 proxy host.
        socks_port: SOCKS5 proxy port as string.
        user: Proxy username (empty string if no auth).
        passwd: Proxy password (empty string if no auth).
    """
    await adb_shell(port, "am force-stop net.typeblog.socks")
    await asyncio.sleep(0.5)
    await adb_shell(port, "monkey -p net.typeblog.socks 1")
    await asyncio.sleep(2.5)

    await sd_set_field(port, "Server IP", host)
    await sd_set_field(port, "Server Port", socks_port)

    if user and passwd:
        wh = await adb_shell(port, "wm size")
        m = re.search(r"(\d+)x(\d+)", wh)
        sw, sh = (540, 960) if not m else (int(m.group(1)) // 2, int(m.group(2)))
        await adb_shell(port, f"input swipe {sw} {sh * 3 // 4} {sw} {sh // 4} 300")
        await asyncio.sleep(0.8)
        await find_and_tap(port, "Username & Password Authentication")
        await asyncio.sleep(0.5)
        await sd_set_field(port, "Username", user)
        await sd_set_field(port, "Password", passwd)

    logger.info("SocksDroid UI configured with {}:{} on port {}", host, socks_port, port)


async def check_socksdroid_configured(port: int, host: str, socks_port: str) -> bool:
    """Quick check if SocksDroid already has the correct IP/Port configured.

    Args:
        port: ADB port of the target instance.
        host: Expected SOCKS5 proxy host.
        socks_port: Expected SOCKS5 proxy port as string.

    Returns:
        True if both host and port are already visible in SocksDroid UI.
    """
    await adb_shell(port, "monkey -p net.typeblog.socks 1")
    await asyncio.sleep(2)
    # Scroll to top to make IP/Port visible
    await adb_shell(port, "input swipe 540 300 540 1500 300")
    await asyncio.sleep(0.5)
    await adb_shell(port, "uiautomator dump /sdcard/ui.xml")
    xml = await adb_shell(port, "cat /sdcard/ui.xml")
    has_ip = f'text="{host}"' in xml
    has_port = f'text="{socks_port}"' in xml
    logger.debug("SocksDroid check: IP={} Port={}", has_ip, has_port)
    return has_ip and has_port


async def enable_socksdroid_vpn(port: int) -> None:
    """Enable VPN toggle in SocksDroid and approve the system dialog if it appears.

    Args:
        port: ADB port of the target instance.
    """
    await adb_shell(port, "uiautomator dump /sdcard/ui.xml")
    xml = await adb_shell(port, "cat /sdcard/ui.xml")
    toggle = re.search(
        r'checkable="true"[^>]*checked="(\w+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml
    )
    if toggle and toggle.group(1) == "true":
        logger.debug("SocksDroid VPN already ON")
        return
    if toggle:
        cx = (int(toggle.group(2)) + int(toggle.group(4))) // 2
        cy = (int(toggle.group(3)) + int(toggle.group(5))) // 2
        await adb_shell(port, f"input tap {cx} {cy}")
        await asyncio.sleep(2)
        await adb_shell(port, "uiautomator dump /sdcard/ui.xml")
        xml_vpn = await adb_shell(port, "cat /sdcard/ui.xml")
        if "Connection request" in xml_vpn:
            ok = re.search(r'text="OK"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_vpn)
            if ok:
                ox = (int(ok.group(1)) + int(ok.group(3))) // 2
                oy = (int(ok.group(2)) + int(ok.group(4))) // 2
                await adb_shell(port, f"input tap {ox} {oy}")
                logger.info("VPN dialog approved on port {}", port)
                await asyncio.sleep(1)


async def verify_proxy_active(port: int) -> str:
    """Check actual external IP via curl inside the emulator.

    Args:
        port: ADB port of the target instance.

    Returns:
        External IP string, or empty string if unreachable.
    """
    await asyncio.sleep(2)
    for url in ("https://api.ipify.org", "https://ifconfig.me"):
        try:
            result = await adb_shell(port, f"curl -s --max-time 10 {url}", timeout=15)
            ip = result.strip()
            if ip and len(ip) < 50 and "." in ip:
                logger.info("Proxy verified: external IP = {}", ip)
                return ip
        except RuntimeError:
            continue
    return ""
