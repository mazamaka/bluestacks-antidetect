"""Device Audit — collect and analyze all detectable emulator markers.

Checks build props, system files, hardware IDs, runtime markers,
and reports what Google can use to detect BlueStacks as an emulator.
"""

import asyncio
from loguru import logger
from adb_manager import adb_shell, connect


async def run_audit(adb_port: int) -> dict:
    """Run full device audit on a running instance.

    Returns dict with categories of checks, each containing
    list of {name, value, status, detail} items.
    Status: 'ok' | 'warn' | 'fail'
    """
    if not await connect(adb_port):
        raise ConnectionError(f"Cannot connect to ADB port {adb_port}")

    results = {
        "build_props": await _check_build_props(adb_port),
        "system_files": await _check_system_files(adb_port),
        "hardware_ids": await _check_hardware_ids(adb_port),
        "runtime": await _check_runtime(adb_port),
        "packages": await _check_packages(adb_port),
        "network": await _check_network(adb_port),
    }

    # Calculate summary
    total = sum(len(v) for v in results.values())
    fails = sum(1 for v in results.values() for i in v if i["status"] == "fail")
    warns = sum(1 for v in results.values() for i in v if i["status"] == "warn")
    oks = total - fails - warns

    results["summary"] = {
        "total": total,
        "ok": oks,
        "warn": warns,
        "fail": fails,
        "score": round(oks / total * 100) if total else 0,
    }

    logger.info("Audit on port {}: {}/{} OK, {} WARN, {} FAIL",
                adb_port, oks, total, warns, fails)
    return results


async def _prop(port: int, prop: str) -> str:
    """Get a property value, return empty string on failure."""
    try:
        return (await adb_shell(port, f"getprop {prop}")).strip()
    except RuntimeError:
        return ""


async def _shell(port: int, cmd: str) -> str:
    """Run shell command, return empty string on failure."""
    try:
        return (await adb_shell(port, cmd)).strip()
    except RuntimeError:
        return ""


def _check(name: str, value: str, bad_values: list[str] | None = None,
           good_values: list[str] | None = None, empty_is_bad: bool = False,
           detail: str = "") -> dict:
    """Create a check result item."""
    status = "ok"
    if bad_values and any(b in value.lower() for b in bad_values):
        status = "fail"
    elif good_values and not any(g in value.lower() for g in good_values):
        status = "warn"
    elif empty_is_bad and not value:
        status = "warn"
    return {"name": name, "value": value or "(empty)", "status": status, "detail": detail}


async def _check_build_props(port: int) -> list[dict]:
    """Check build properties that reveal emulator."""
    checks = []

    # Critical emulator markers
    hw = await _prop(port, "ro.hardware")
    checks.append(_check("ro.hardware", hw,
                         bad_values=["ranchu", "goldfish", "vbox"],
                         good_values=["qcom", "exynos", "mt", "kirin"],
                         detail="Hardware platform. ranchu/goldfish = emulator"))

    boot_hw = await _prop(port, "ro.boot.hardware")
    checks.append(_check("ro.boot.hardware", boot_hw,
                         bad_values=["ranchu", "goldfish"],
                         detail="Boot hardware"))

    board = await _prop(port, "ro.product.board")
    checks.append(_check("ro.product.board", board,
                         bad_values=["goldfish", "unknown"],
                         empty_is_bad=True,
                         detail="Product board. Should be real chipset name"))

    platform = await _prop(port, "ro.board.platform")
    checks.append(_check("ro.board.platform", platform,
                         bad_values=["generic"],
                         empty_is_bad=True,
                         detail="Board platform (msm8998, exynos990, etc)"))

    chipname = await _prop(port, "ro.hardware.chipname")
    checks.append(_check("ro.hardware.chipname", chipname,
                         empty_is_bad=True,
                         detail="Chip name (snapdragon, exynos, etc)"))

    # Build fingerprint
    fp = await _prop(port, "ro.build.fingerprint")
    checks.append(_check("ro.build.fingerprint", fp,
                         bad_values=["generic", "bluestacks", "vbox", "test-keys"],
                         good_values=["release-keys"],
                         detail="Build fingerprint. Should match real device"))

    display = await _prop(port, "ro.build.display.id")
    checks.append(_check("ro.build.display.id", display,
                         bad_values=["bluestacks", "generic"],
                         detail="Build display ID"))

    # Device identity
    brand = await _prop(port, "ro.product.brand")
    checks.append(_check("ro.product.brand", brand,
                         bad_values=["generic"],
                         good_values=["samsung", "google", "xiaomi", "oneplus", "huawei"],
                         detail="Product brand"))

    model = await _prop(port, "ro.product.model")
    checks.append(_check("ro.product.model", model,
                         bad_values=["sdk", "emulator", "generic"],
                         detail="Product model"))

    device = await _prop(port, "ro.product.device")
    checks.append(_check("ro.product.device", device,
                         bad_values=["generic", "emulator"],
                         detail="Product device codename"))

    # QEMU marker
    qemu = await _prop(port, "ro.kernel.qemu")
    checks.append(_check("ro.kernel.qemu", qemu,
                         bad_values=["1"],
                         detail="QEMU marker. 1 = definitely emulator"))

    secure = await _prop(port, "ro.secure")
    checks.append(_check("ro.secure", secure,
                         bad_values=["0"],
                         good_values=["1"],
                         detail="Secure mode. 0 = debug/development build"))

    debuggable = await _prop(port, "ro.debuggable")
    checks.append(_check("ro.debuggable", debuggable,
                         bad_values=["1"],
                         good_values=["0"],
                         detail="Debuggable. 1 = development build"))

    return checks


async def _check_system_files(port: int) -> list[dict]:
    """Check for emulator-specific files."""
    checks = []

    emu_files = [
        ("/dev/goldfish_pipe", "Goldfish pipe device (emulator)"),
        ("/dev/qemu_pipe", "QEMU pipe device"),
        ("/system/bin/qemu-props", "QEMU properties binary"),
        ("/system/lib/libc_malloc_debug_qemu.so", "QEMU malloc debug lib"),
        ("/system/bin/generic_x86/linker", "Generic x86 linker"),
        ("/dev/vport0p1", "Virtio port (QEMU)"),
    ]

    for path, desc in emu_files:
        exists = await _shell(port, f"test -e {path} && echo YES || echo NO")
        checks.append(_check(path, "exists" if "YES" in exists else "not found",
                             bad_values=["exists"],
                             detail=desc))

    return checks


async def _check_hardware_ids(port: int) -> list[dict]:
    """Check hardware identifiers."""
    checks = []

    # Android ID
    android_id = await _shell(port, "settings get secure android_id")
    checks.append(_check("android_id", android_id,
                         empty_is_bad=True,
                         detail="Unique Android ID"))

    # GSF ID
    gsf_id = await _shell(port, "settings get secure google_services_framework_id 2>/dev/null || echo ''")
    checks.append(_check("gsf_id", gsf_id,
                         empty_is_bad=True,
                         detail="Google Services Framework ID"))

    # Serial
    serial = await _prop(port, "ro.serialno")
    checks.append(_check("ro.serialno", serial,
                         bad_values=["unknown", "emulator", "123456"],
                         empty_is_bad=True,
                         detail="Device serial number"))

    # IMEI (may not be accessible without phone permission)
    imei = await _shell(port, "service call iphonesubinfo 1 2>/dev/null | grep -oE '[0-9a-f]{8}' | tail -1")
    checks.append(_check("IMEI", imei or "(not accessible)",
                         detail="Phone IMEI (needs telephony)"))

    return checks


async def _check_runtime(port: int) -> list[dict]:
    """Check runtime environment markers."""
    checks = []

    # CPU info
    cpuinfo = await _shell(port, "cat /proc/cpuinfo | head -10")
    is_qemu = "qemu" in cpuinfo.lower() or "kvm" in cpuinfo.lower()
    cpu_model = ""
    for line in cpuinfo.split("\n"):
        if "model name" in line.lower() or "hardware" in line.lower():
            cpu_model = line.split(":")[-1].strip() if ":" in line else line
            break
    checks.append(_check("/proc/cpuinfo", cpu_model or cpuinfo[:80],
                         bad_values=["qemu", "kvm", "virtual", "generic"],
                         good_values=["qualcomm", "snapdragon", "exynos", "arm"],
                         detail="CPU identification"))

    # OpenGL renderer
    gl = await _prop(port, "ro.hardware.egl")
    gl_renderer = await _shell(port, "dumpsys SurfaceFlinger 2>/dev/null | grep -i 'GLES' | head -1")
    checks.append(_check("GL Renderer", gl_renderer or gl or "(unknown)",
                         bad_values=["swiftshader", "virtio", "llvmpipe", "mesa", "android emulator"],
                         good_values=["adreno", "mali", "powervr"],
                         detail="OpenGL renderer. SwiftShader/Virtio = emulator"))

    # Sensors
    sensors = await _shell(port, "dumpsys sensorservice 2>/dev/null | grep -c 'handle' || echo 0")
    try:
        sensor_count = int(sensors.strip())
    except ValueError:
        sensor_count = 0
    status = "ok" if sensor_count >= 10 else "warn" if sensor_count >= 3 else "fail"
    checks.append({"name": "Sensor count", "value": str(sensor_count),
                   "status": status,
                   "detail": f"Real devices have 15-30 sensors. Emulators: 0-5"})

    # Battery
    battery = await _shell(port, "dumpsys battery 2>/dev/null | grep 'AC powered\\|USB powered\\|status'")
    checks.append(_check("Battery", battery or "(unknown)",
                         detail="Battery status (emulators often show AC powered)"))

    return checks


async def _check_packages(port: int) -> list[dict]:
    """Check for BlueStacks-specific packages."""
    checks = []

    bs_packages = [
        "com.bluestacks.home",
        "com.bluestacks.settings",
        "com.bluestacks.gamecenter",
        "com.bluestacks.appmart",
        "com.bluestacks.BstCommandProcessor",
        "com.bluestacks.filemanager",
        "com.uncube.launcher",
        "com.uncube.launcher3",
    ]

    all_packages = await _shell(port, "pm list packages")
    found = []
    for pkg in bs_packages:
        if pkg in all_packages:
            found.append(pkg)

    if found:
        checks.append(_check("BlueStacks packages", f"{len(found)} found: {', '.join(found[:3])}...",
                             bad_values=[str(len(found))],  # any found is bad
                             detail="BlueStacks-specific packages visible to apps"))
    else:
        checks.append(_check("BlueStacks packages", "none found",
                             detail="No BlueStacks packages detected"))
        checks[-1]["status"] = "ok"

    # Force the status based on found count
    if found:
        checks[-1]["status"] = "fail"

    return checks


async def _check_network(port: int) -> list[dict]:
    """Check network/carrier info."""
    checks = []

    # Carrier
    operator = await _prop(port, "gsm.operator.alpha")
    checks.append(_check("Carrier", operator,
                         empty_is_bad=True,
                         detail="Mobile carrier name. Empty = no SIM"))

    mcc = await _prop(port, "gsm.sim.operator.numeric")
    checks.append(_check("MCC/MNC", mcc,
                         empty_is_bad=True,
                         detail="Mobile Country/Network Code"))

    # Proxy
    proxy = await _shell(port, "settings get global http_proxy")
    is_proxy = proxy and proxy != "null" and proxy != ":0"
    checks.append(_check("HTTP Proxy", proxy if is_proxy else "none",
                         bad_values=["10.0.2.2"] if is_proxy else [],
                         detail="System HTTP proxy. 10.0.2.2 = host bridge (detectable)"))

    # WiFi MAC
    mac = await _shell(port, "cat /sys/class/net/wlan0/address 2>/dev/null || echo ''")
    checks.append(_check("WiFi MAC", mac,
                         bad_values=["00:00:00:00:00:00", "02:00:00:00:00:00"],
                         empty_is_bad=True,
                         detail="WiFi MAC address"))

    return checks
