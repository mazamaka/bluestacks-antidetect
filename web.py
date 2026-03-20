"""Web UI & API for BlueStacks Antidetect Manager."""

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from pydantic import BaseModel

from adb_manager import adb_cmd, adb_shell, clear_proxy, connect, get_device_info, install_apk
from bs_conf import parse_conf, write_conf
from config import BS_MAIN
from fingerprint import generate_fingerprint
from instance_manager import InstanceManager
from proxy_bridge import get_bridge_status, start_bridge, stop_all_bridges
from proxy_manager import (
    assign_proxy,
    batch_validate_proxies,
    check_ip,
    filter_proxy_lines,
    load_profiles,
    remove_proxy,
    save_profiles,
    validate_proxy,
)
from socksdroid import (
    check_socksdroid_configured,
    configure_socksdroid_ui,
    enable_socksdroid_vpn,
    verify_proxy_active,
)

# Loguru config
logger.remove()
logger.add(
    sys.stderr, level="DEBUG",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
)

mgr = InstanceManager()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    logger.info("BlueStacks Antidetect Web UI started on port 8899")
    yield
    logger.info("Shutting down, stopping all bridges...")
    stop_all_bridges()


app = FastAPI(title="BlueStacks Antidetect", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# --- Models ---

class CreateInstanceRequest(BaseModel):
    name: str
    count: int = 1
    cpus: int = 4
    ram: int = 4096


class ProxyRequest(BaseModel):
    proxy: str  # socks5://user:pass@host:port or host:port:user:pass


class BatchProxyRequest(BaseModel):
    proxies: dict[str, str]  # {instance_name: proxy_string}


class CheckProxyRequest(BaseModel):
    proxy: str


class BatchCheckProxyRequest(BaseModel):
    proxies: list[str]


# --- Web UI ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


# --- Instance API ---

@app.get("/api/instances")
async def api_list_instances() -> dict:
    mgr.reload_conf()
    instances = mgr.list_instances()
    profiles = load_profiles()
    for inst in instances:
        profile = profiles.get(inst["name"], {})
        inst["proxy"] = profile.get("proxy", "")
        inst["proxy_ip"] = profile.get("proxy_ip", "")
        inst["proxy_country"] = profile.get("proxy_country", "")
        inst["proxy_city"] = profile.get("proxy_city", "")
        inst["bridge_port"] = profile.get("bridge_port", "")
    return {"instances": instances}


@app.post("/api/instances")
async def api_create_instance(req: CreateInstanceRequest) -> dict:
    mgr.reload_conf()
    results: list[dict] = []
    for i in range(1, req.count + 1):
        name = req.name if req.count == 1 else f"{req.name}{i}"
        try:
            info = mgr.create_instance(name, cpus=req.cpus, ram=req.ram)
            results.append({
                "name": info["name"],
                "display_name": info.get("display_name", name),
                "device": info["device"],
                "adb_port": info["adb_port"],
                "status": "created",
            })
        except (FileNotFoundError, subprocess.CalledProcessError, OSError) as e:
            results.append({"name": name, "status": "error", "error": str(e)})
    return {"results": results}


@app.delete("/api/instances/{name}")
async def api_delete_instance(name: str) -> dict:
    mgr.reload_conf()
    try:
        mgr.delete_instance(name)
        return {"status": "deleted", "name": name}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/instances/{name}/start")
async def api_start_instance(name: str) -> dict:
    """Launch a BlueStacks instance.

    If proxy assigned: validate, start/restart bridge, then launch.
    Background task auto-applies proxy via ADB after boot.
    """
    profiles = load_profiles()
    profile = profiles.get(name, {})

    # If proxy assigned -- validate and ensure bridge is running
    if profile.get("proxy"):
        proxy_str = profile["proxy"]
        check = await validate_proxy(proxy_str)
        if not check["valid"]:
            raise HTTPException(
                400,
                f"Proxy not working: {check['error']}. Instance not started.",
            )

        # Always recreate bridge on Start to avoid stale/dead processes
        bridge_port = start_bridge(
            name, profile["proxy_host"], profile["proxy_port"],
            profile.get("proxy_user"), profile.get("proxy_pass"),
        )
        profile["bridge_port"] = bridge_port
        profile["proxy_ip"] = check.get("ip", "")
        profile["proxy_country"] = check.get("country", "")
        profile["proxy_city"] = check.get("city", "")
        save_profiles(profiles)
        logger.info("Proxy validated for '{}': {} ({})", name, check["ip"], check["country"])

    try:
        subprocess.Popen(
            [str(BS_MAIN), "--instance", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("Launched instance '{}'", name)

        # Auto-apply proxy in background after boot
        if profile.get("proxy") and profile.get("bridge_port"):
            asyncio.create_task(_wait_and_apply_proxy(name, profile))

        if profile.get("proxy"):
            msg = "Starting with proxy {} ({}). Auto-applying after boot...".format(
                profile.get("proxy_ip", ""), profile.get("proxy_country", ""),
            )
        else:
            msg = "Starting..."
        return {"status": "ok", "instance": name, "message": msg}
    except FileNotFoundError:
        raise HTTPException(400, f"BlueStacks binary not found at {BS_MAIN}")
    except OSError as e:
        raise HTTPException(400, str(e))


@app.post("/api/instances/{name}/stop")
async def api_stop_instance(name: str) -> dict:
    """Stop a BlueStacks instance via ADB reboot -p (poweroff)."""
    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))
    if not adb_port:
        raise HTTPException(404, f"Instance '{name}' not found")
    try:
        if await connect(adb_port):
            await adb_shell(adb_port, "reboot -p")
            return {"status": "ok", "instance": name, "message": "Stopping..."}
        return {"status": "error", "message": "Instance not running"}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


# --- Proxy API ---

@app.post("/api/instances/{name}/proxy")
async def api_set_proxy(name: str, req: ProxyRequest) -> dict:
    try:
        await assign_proxy(name, req.proxy)
        return {"status": "ok", "instance": name, "proxy": req.proxy}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/instances/{name}/proxy")
async def api_remove_proxy(name: str) -> dict:
    try:
        await remove_proxy(name)
        return {"status": "ok", "instance": name}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/instances/{name}/reapply-proxy")
async def api_reapply_proxy(name: str) -> dict:
    """Re-apply saved proxy via ADB (useful when instance becomes available)."""
    profiles = load_profiles()
    profile = profiles.get(name)
    if not profile or not profile.get("proxy"):
        raise HTTPException(400, f"No proxy saved for '{name}'")

    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))

    # Ensure bridge is running (recreate if dead)
    bridge = get_bridge_status(name)
    bridge_port = profile.get("bridge_port", _BASE_BRIDGE_PORT_FALLBACK)
    if not bridge or not bridge.get("running"):
        bridge_port = start_bridge(
            name, profile["proxy_host"], profile["proxy_port"],
            profile.get("proxy_user"), profile.get("proxy_pass"),
        )
        profile["bridge_port"] = bridge_port
        save_profiles(profiles)
    else:
        bridge_port = bridge["local_port"]

    try:
        if await connect(adb_port):
            await adb_shell(adb_port, f"settings put global http_proxy 10.0.2.2:{bridge_port}")
            return {"status": "ok", "message": f"Proxy applied via ADB (10.0.2.2:{bridge_port})"}
        return {
            "status": "error",
            "message": f"ADB offline on port {adb_port}. Enable ADB in BlueStacks Settings -> Advanced.",
        }
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/instances/{name}/ip")
async def api_check_ip(name: str) -> dict:
    try:
        ip = await check_ip(name)
        return {"instance": name, "ip": ip}
    except (ValueError, ConnectionError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/instances/{name}/info")
async def api_instance_info(name: str) -> dict:
    conf = parse_conf()
    port_key = f"bst.instance.{name}.adb_port"
    if port_key not in conf:
        raise HTTPException(404, f"Instance '{name}' not found")
    port = int(conf[port_key])
    try:
        if not await connect(port):
            return {"instance": name, "status": "offline", "adb_port": port}
        info = await get_device_info(port)
        return {"instance": name, "status": "online", "adb_port": port, "info": info}
    except RuntimeError as e:
        return {"instance": name, "status": "error", "error": str(e)}


@app.get("/api/fingerprint")
async def api_generate_fingerprint() -> dict:
    return generate_fingerprint()


@app.post("/api/instances/batch-proxy")
async def api_batch_proxy(req: BatchProxyRequest) -> dict:
    results: list[dict] = []
    for instance_name, proxy_str in req.proxies.items():
        try:
            await assign_proxy(instance_name, proxy_str)
            results.append({"instance": instance_name, "status": "ok"})
        except ValueError as e:
            results.append({"instance": instance_name, "status": "error", "error": str(e)})
    return {"results": results}


@app.post("/api/proxy/check")
async def api_check_proxy(req: CheckProxyRequest) -> dict:
    return await validate_proxy(req.proxy)


@app.post("/api/proxy/batch-check")
async def api_batch_check_proxy(req: BatchCheckProxyRequest) -> dict:
    # Filter junk lines before validation
    clean_proxies = filter_proxy_lines("\n".join(req.proxies))
    if not clean_proxies:
        return {"results": [], "filtered_out": len(req.proxies)}
    results = await batch_validate_proxies(clean_proxies)
    return {"results": results, "filtered_out": len(req.proxies) - len(clean_proxies)}


@app.post("/api/instances/{name}/setup-socksdroid")
async def api_setup_socksdroid(name: str) -> dict:
    """Install SocksDroid, configure proxy via UI automation, enable VPN, verify IP."""
    apk_path = Path(__file__).parent / "apks" / "socksdroid.apk"
    if not apk_path.exists():
        raise HTTPException(404, f"SocksDroid APK not found at {apk_path}")

    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))
    if not adb_port:
        raise HTTPException(404, f"Instance '{name}' not found")

    try:
        if not await connect(adb_port):
            raise HTTPException(400, "Instance not running or ADB offline")

        # Install if needed
        result = await adb_shell(adb_port, "pm list packages net.typeblog.socks")
        if "net.typeblog.socks" not in result:
            await install_apk(adb_port, str(apk_path))
            logger.info("SocksDroid installed on '{}'", name)
            await asyncio.sleep(1)

        # Get proxy from profile
        profiles = load_profiles()
        profile = profiles.get(name, {})
        proxy_str = profile.get("proxy", "")
        host = profile.get("proxy_host", "")
        port = str(profile.get("proxy_port", ""))
        user = profile.get("proxy_user", "")
        passwd = profile.get("proxy_pass", "")

        if proxy_str and host and port:
            # Quick check: is proxy already active? (fastest path)
            current_ip = await verify_proxy_active(adb_port)
            expected_ip = profile.get("proxy_ip", "")
            if current_ip and expected_ip and current_ip == expected_ip:
                await adb_shell(adb_port, "input keyevent KEYCODE_HOME")
                return {
                    "status": "ok", "instance": name,
                    "message": f"Proxy already active — IP: {current_ip}",
                    "ip": current_ip,
                }

            # Check if already configured correctly
            already_ok = await check_socksdroid_configured(adb_port, host, port)

            if already_ok:
                logger.info("SocksDroid already configured on '{}', fast path", name)
            else:
                logger.info("SocksDroid needs full setup on '{}'", name)
                await configure_socksdroid_ui(adb_port, host, port, user, passwd)

            # Enable VPN toggle
            await enable_socksdroid_vpn(adb_port)

            # Clear system HTTP proxy (SocksDroid uses VPN)
            await clear_proxy(adb_port)

            # Verify actual IP
            actual_ip = await verify_proxy_active(adb_port)

            # Press Home to minimize SocksDroid
            await adb_shell(adb_port, "input keyevent KEYCODE_HOME")

            if actual_ip:
                ip_match = actual_ip == expected_ip if expected_ip else True
                status = "ok" if ip_match else "warning"
                msg = f"SocksDroid ON — IP: {actual_ip}"
                if not ip_match and expected_ip:
                    msg += f" (expected {expected_ip})"
                return {"status": status, "instance": name, "message": msg, "ip": actual_ip}
            return {
                "status": "warning", "instance": name,
                "message": f"SocksDroid configured ({host}:{port}) but IP check failed",
            }
        else:
            await adb_shell(adb_port, "monkey -p net.typeblog.socks 1")
            return {"status": "ok", "instance": name, "message": "SocksDroid installed — set proxy manually"}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/instances/{name}/cloak")
async def api_cloak(name: str) -> dict:
    """Apply Phase 1 cloaking (hide BS packages, fix props, MAC)."""
    from cloaking import apply_cloaking
    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))
    if not adb_port:
        raise HTTPException(404, f"Instance '{name}' not found")

    # Get device model for this instance
    model_key = f"bst.instance.{name}.device_custom_model"
    brand_key = f"bst.instance.{name}.device_custom_brand"
    model = conf.get(model_key, "")
    brand = conf.get(brand_key, "")
    # Build full model string for lookup
    full_model = f"{brand} {model}".strip() if brand else model

    try:
        result = await apply_cloaking(adb_port, full_model, brand)
        fixes = result["fixes"]
        msg = " | ".join(f"{f['name']}: {f['status']}" for f in fixes)
        return {"status": "ok", "instance": name, "message": msg, "fixes": fixes,
                "ok": result["ok"], "fail": result["fail"]}
    except (ConnectionError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/instances/{name}/cloak-revert")
async def api_cloak_revert(name: str) -> dict:
    """Revert cloaking fixes."""
    from cloaking import revert_cloaking
    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))
    if not adb_port:
        raise HTTPException(404, f"Instance '{name}' not found")
    try:
        result = await revert_cloaking(adb_port)
        return {"status": "ok", "instance": name, "fixes": result["fixes"]}
    except (ConnectionError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/instances/{name}/install-apps")
async def api_install_apps(name: str) -> dict:
    """Install Play Console + Play Integrity Checker on instance."""
    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))
    if not adb_port:
        raise HTTPException(404, f"Instance '{name}' not found")

    try:
        if not await connect(adb_port):
            raise HTTPException(400, "Instance not running or ADB offline")

        apks_dir = Path(__file__).parent / "apks"
        results = []

        # Play Integrity Checker
        pkg = "gr.nikolasspyr.integritycheck"
        check = await adb_shell(adb_port, f"pm list packages {pkg}")
        if pkg in check:
            results.append(f"Integrity Checker: already installed")
        else:
            ic_dir = apks_dir / "integrity-check"
            apk_files = sorted(ic_dir.glob("*.apk"))
            if apk_files:
                paths = " ".join(str(f) for f in apk_files)
                await adb_cmd(adb_port, "install-multiple", "-r", *[str(f) for f in apk_files], timeout=60)
                results.append(f"Integrity Checker: installed")
            else:
                results.append(f"Integrity Checker: APK not found")

        # Play Console
        pkg = "com.google.android.apps.playconsole"
        check = await adb_shell(adb_port, f"pm list packages {pkg}")
        if pkg in check:
            results.append(f"Play Console: already installed")
        else:
            pc_dir = apks_dir / "play-console"
            apk_files = sorted(pc_dir.glob("*.apk"))
            if apk_files:
                await adb_cmd(adb_port, "install-multiple", "-r", *[str(f) for f in apk_files], timeout=120)
                results.append(f"Play Console: installed")
            else:
                results.append(f"Play Console: APK not found")

        return {"status": "ok", "instance": name, "message": " | ".join(results)}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/instances/{name}/audit")
async def api_device_audit(name: str) -> dict:
    """Run device audit — check all emulator detection markers."""
    from device_audit import run_audit
    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))
    if not adb_port:
        raise HTTPException(404, f"Instance '{name}' not found")
    try:
        return await run_audit(adb_port)
    except ConnectionError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/adb-enable")
async def api_adb_enable() -> dict:
    mgr.reload_conf()
    mgr.conf["bst.enable_adb_access"] = "1"
    write_conf(mgr.conf)
    return {"status": "ok", "message": "ADB enabled. Restart BlueStacks."}


# --- Background tasks ---

_BASE_BRIDGE_PORT_FALLBACK = 18800


async def _wait_and_apply_proxy(name: str, profile: dict) -> None:
    """Background task: wait for ADB to come online, then apply HTTP proxy."""
    conf = parse_conf()
    adb_port = int(conf.get(f"bst.instance.{name}.adb_port", "0"))
    bridge_port = profile.get("bridge_port", _BASE_BRIDGE_PORT_FALLBACK)

    logger.debug("Waiting for ADB on '{}' (port {}) to apply proxy...", name, adb_port)

    for attempt in range(30):  # ~60 seconds
        await asyncio.sleep(2)
        try:
            if await connect(adb_port):
                await adb_shell(adb_port, f"settings put global http_proxy 10.0.2.2:{bridge_port}")
                logger.info("Auto-applied proxy on '{}' via ADB (attempt {})", name, attempt + 1)
                return
        except RuntimeError:
            continue
    logger.warning("Failed to auto-apply proxy on '{}' after 30 attempts", name)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web:app", host="0.0.0.0", port=8899, reload=True)
