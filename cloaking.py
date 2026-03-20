"""Device Cloaking — hide emulator markers without root.

Phase 1: Safe fixes via ADB (setprop, pm hide, ip link).
All changes are reversible via revert_all() or reboot.
"""

import asyncio
from loguru import logger
from adb_manager import adb_shell, connect


# Props that should match the device profile
DEVICE_PROPS = {
    "Samsung SM-G991B": {
        "ro.hardware.chipname": "exynos2100",
        "ro.board.platform": "universal2100",
        "ro.product.board": "exynos2100",
    },
    "Samsung SM-S908B": {
        "ro.hardware.chipname": "exynos2200",
        "ro.board.platform": "universal2200",
        "ro.product.board": "exynos2200",
    },
    "Samsung SM-A536B": {
        "ro.hardware.chipname": "exynos1280",
        "ro.board.platform": "universal1280",
        "ro.product.board": "exynos1280",
    },
    "Samsung SM-G780G": {
        "ro.hardware.chipname": "exynos990",
        "ro.board.platform": "universal990",
        "ro.product.board": "exynos990",
    },
    "Samsung SM-A546B": {
        "ro.hardware.chipname": "exynos1380",
        "ro.board.platform": "universal1380",
        "ro.product.board": "exynos1380",
    },
    "Google Pixel 7": {
        "ro.hardware.chipname": "gs201",
        "ro.board.platform": "gs201",
        "ro.product.board": "panther",
    },
    "Google Pixel 8 Pro": {
        "ro.hardware.chipname": "gs301",
        "ro.board.platform": "gs301",
        "ro.product.board": "husky",
    },
    "Google Pixel 6a": {
        "ro.hardware.chipname": "gs101",
        "ro.board.platform": "gs101",
        "ro.product.board": "bluejay",
    },
    "Xiaomi 2201117TG": {
        "ro.hardware.chipname": "sm6375",
        "ro.board.platform": "holi",
        "ro.product.board": "holi",
    },
    "OnePlus CPH2449": {
        "ro.hardware.chipname": "sm8475",
        "ro.board.platform": "taro",
        "ro.product.board": "taro",
    },
}

# BlueStacks packages to hide
# NOTE: DO NOT hide launchers (com.uncube.launcher*) — breaks home screen!
BS_PACKAGES = [
    "com.bluestacks.home",
    "com.bluestacks.settings",
    "com.bluestacks.gamecenter",
    "com.bluestacks.appmart",
    "com.bluestacks.BstCommandProcessor",
    "com.bluestacks.filemanager",
]
# Packages that should NEVER be hidden (break BlueStacks)
BS_SAFE_SKIP = [
    "com.uncube.launcher",
    "com.uncube.launcher3",
]

# Random MAC prefixes from real vendors (Samsung, Google, Xiaomi, OnePlus)
MAC_PREFIXES = {
    "Samsung": ["A8:7D:12", "4C:BC:98", "B4:3A:28", "90:18:7C"],
    "Google": ["F4:F5:D8", "3C:28:6D", "54:60:09"],
    "Xiaomi": ["50:64:2B", "28:6C:07", "7C:A1:77"],
    "OnePlus": ["94:65:2D", "C0:EE:FB"],
}


async def apply_cloaking(adb_port: int, model: str, brand: str = "") -> dict:
    """Apply all Phase 1 cloaking fixes.

    Args:
        adb_port: ADB port of running instance.
        model: Device model (e.g. "Samsung SM-G991B").
        brand: Device brand for MAC prefix selection.

    Returns:
        Dict with results for each fix.
    """
    if not await connect(adb_port):
        raise ConnectionError(f"Cannot connect to ADB port {adb_port}")

    results = []

    # 1. Hide BlueStacks packages
    r = await _hide_bs_packages(adb_port)
    results.append(r)

    # 2. Set chipname/platform props
    r = await _set_device_props(adb_port, model)
    results.append(r)

    # 3. Set WiFi MAC
    r = await _set_wifi_mac(adb_port, brand)
    results.append(r)

    # 4. Fix carrier if empty
    r = await _fix_carrier(adb_port)
    results.append(r)

    # Count
    ok = sum(1 for r in results if r["status"] == "ok")
    fail = sum(1 for r in results if r["status"] == "fail")

    logger.info("Cloaking applied on port {}: {}/{} OK", adb_port, ok, len(results))
    return {"fixes": results, "ok": ok, "fail": fail, "total": len(results)}


async def revert_cloaking(adb_port: int) -> dict:
    """Revert all cloaking fixes."""
    if not await connect(adb_port):
        raise ConnectionError(f"Cannot connect to ADB port {adb_port}")

    results = []

    # 1. Unhide packages
    r = await _unhide_bs_packages(adb_port)
    results.append(r)

    # 2. Props revert on reboot (setprop is not persistent)
    results.append({"name": "Device props", "status": "ok",
                    "detail": "Will reset on reboot (setprop is not persistent)"})

    # 3. MAC revert on reboot
    results.append({"name": "WiFi MAC", "status": "ok",
                    "detail": "Will reset on reboot"})

    logger.info("Cloaking reverted on port {}", adb_port)
    return {"fixes": results}


async def _hide_bs_packages(port: int) -> dict:
    """Hide BlueStacks-specific packages."""
    hidden = []
    errors = []
    all_packages = await adb_shell(port, "pm list packages")

    for pkg in BS_PACKAGES:
        if pkg in all_packages:
            try:
                await adb_shell(port, f"pm hide {pkg}")
                hidden.append(pkg)
            except RuntimeError as e:
                # pm hide may need higher privileges, try disable
                try:
                    await adb_shell(port, f"pm disable-user --user 0 {pkg}")
                    hidden.append(pkg)
                except RuntimeError:
                    errors.append(pkg)

    if hidden:
        detail = f"Hidden: {', '.join(hidden)}"
        if errors:
            detail += f" | Failed: {', '.join(errors)}"
        return {"name": "Hide BS packages", "status": "ok", "detail": detail}
    elif errors:
        return {"name": "Hide BS packages", "status": "fail",
                "detail": f"Failed to hide: {', '.join(errors)}"}
    else:
        return {"name": "Hide BS packages", "status": "ok",
                "detail": "No BS packages found"}


async def _unhide_bs_packages(port: int) -> dict:
    """Unhide BlueStacks packages."""
    restored = []
    for pkg in BS_PACKAGES:
        try:
            await adb_shell(port, f"pm unhide {pkg}")
            await adb_shell(port, f"pm enable {pkg}")
            restored.append(pkg)
        except RuntimeError:
            pass

    return {"name": "Unhide BS packages", "status": "ok",
            "detail": f"Restored: {len(restored)} packages"}


async def _set_device_props(port: int, model: str) -> dict:
    """Set device-specific props (chipname, platform, board)."""
    # Find matching device profile
    props = DEVICE_PROPS.get(model)
    if not props:
        # Try partial match
        for key, val in DEVICE_PROPS.items():
            if model in key or key in model:
                props = val
                break

    if not props:
        return {"name": "Device props", "status": "warn",
                "detail": f"No profile for model '{model}'"}

    set_props = []
    for prop, value in props.items():
        try:
            await adb_shell(port, f"setprop {prop} {value}")
        except RuntimeError:
            pass  # setprop returns error for ro.* but value may still be set
        # Verify it actually took
        try:
            actual = (await adb_shell(port, f"getprop {prop}")).strip()
            if actual == value:
                set_props.append(f"{prop}={value}")
        except RuntimeError:
            pass

    if set_props:
        return {"name": "Device props", "status": "ok",
                "detail": f"Set: {', '.join(set_props)}"}
    return {"name": "Device props", "status": "fail", "detail": "Failed to set props"}


async def _set_wifi_mac(port: int, brand: str) -> dict:
    """Set WiFi MAC address from real vendor prefix."""
    import random

    # Pick vendor prefix
    prefixes = MAC_PREFIXES.get(brand, MAC_PREFIXES.get("Samsung", ["A8:7D:12"]))
    prefix = random.choice(prefixes)
    suffix = ":".join(f"{random.randint(0, 255):02X}" for _ in range(3))
    mac = f"{prefix}:{suffix}"

    # Check if wlan0 exists
    iface = await adb_shell(port, "ls /sys/class/net/ 2>/dev/null")
    if "wlan0" not in iface:
        return {"name": "WiFi MAC", "status": "warn",
                "detail": "No wlan0 interface found"}

    try:
        await adb_shell(port, f"ip link set wlan0 down")
        await adb_shell(port, f"ip link set wlan0 address {mac}")
        await adb_shell(port, f"ip link set wlan0 up")
        return {"name": "WiFi MAC", "status": "ok", "detail": f"Set: {mac}"}
    except RuntimeError as e:
        return {"name": "WiFi MAC", "status": "fail",
                "detail": f"Cannot set MAC (need root): {e}"}


async def _fix_carrier(port: int) -> dict:
    """Set carrier/operator info if missing."""
    current = await adb_shell(port, "getprop gsm.operator.alpha")
    if current and current != "(empty)":
        return {"name": "Carrier", "status": "ok",
                "detail": f"Already set: {current}"}

    # Set realistic carrier based on existing MCC/MNC or default
    try:
        await adb_shell(port, "setprop gsm.operator.alpha 'T-Mobile'")
        await adb_shell(port, "setprop gsm.sim.operator.alpha 'T-Mobile'")
        await adb_shell(port, "setprop gsm.operator.numeric '310260'")
        await adb_shell(port, "setprop gsm.sim.operator.numeric '310260'")
        await adb_shell(port, "setprop gsm.operator.iso-country 'us'")
        return {"name": "Carrier", "status": "ok",
                "detail": "Set: T-Mobile US (310260)"}
    except RuntimeError as e:
        return {"name": "Carrier", "status": "fail", "detail": str(e)}
