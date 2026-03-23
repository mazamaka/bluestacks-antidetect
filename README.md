<div align="center">

# BlueStacks Antidetect Manager

**Android antidetect system — instance cloning, device fingerprinting, emulator cloaking, proxy bridge**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg?logo=apple&logoColor=white)]()

A full-featured antidetect manager for BlueStacks Android emulator. Create isolated instances with unique device fingerprints, hide emulator markers, route traffic through SOCKS5 proxies, and audit detection risk — all from a web UI.

[Features](#features) |
[Architecture](#architecture) |
[Quick Start](#quick-start) |
[API Reference](#api-reference) |
[How It Works](#how-it-works)

</div>

---

## Why This Exists

Running multiple Android instances for testing, development, or automation requires each instance to appear as a unique, real device. Standard emulators leak dozens of detection markers (goldfish pipes, QEMU properties, generic hardware IDs) that antifraud systems flag instantly.

**BlueStacks Antidetect Manager** solves this by:
- Generating realistic device fingerprints from **11 real device profiles** (Samsung, Google, Xiaomi, OnePlus)
- Hiding emulator markers at the system property level via **Magisk resetprop**
- Routing traffic through **SOCKS5 proxies** with a local HTTP bridge
- Providing a **detection audit** that scores 45+ markers across 6 categories

---

## Features

### Instance Management
- Create/delete isolated Android instances with **QCOW2 copy-on-write** disks (instant cloning)
- Batch creation (spin up 10+ instances at once)
- Configurable CPU cores, RAM, screen resolution per instance
- Start/stop/restart from web UI

### Device Fingerprinting
- **11 real device profiles**: Samsung (S21, S22 Ultra, S20 FE, A53, A54), Google (Pixel 7, 8 Pro, 6a), Xiaomi, OnePlus
- **7 screen resolutions** (720x1280 → 1440x2560) with matching DPI
- Realistic **IMEI generation** with Luhn checksum validation
- Unique Android ID, Google Advertising ID (UUID v4), MAC address (real vendor OUIs), device serial
- Full **build.prop** override: brand, model, device, board, chipname, timezone, language

### Emulator Cloaking (rootless via Magisk)
- Fix security properties (`ro.secure=1`, `ro.debuggable=0`, `ro.build.type=user`)
- Set device-specific hardware props (chipname, board platform matching the profile)
- Hide BlueStacks system packages (`pm hide` — 6 packages, preserves launcher)
- Spoof WiFi MAC address with real vendor OUI prefixes
- Set carrier info (T-Mobile/310260 by default)
- Reversible on reboot

### Proxy Management
- **SOCKS5 proxy** support with username/password authentication
- Local **HTTP → SOCKS5 bridge** (multiprocessing, one process per instance)
- Proxy validation via SOCKS5 connect test + ipinfo.io (IP, country, city, latency)
- Batch validation (parallel check of 100+ proxies)
- Auto-apply HTTP proxy on Android after boot
- Flexible format parsing: `socks5://user:pass@host:port`, `host:port:user:pass`, etc.

### SocksDroid VPN Automation
- Auto-install SocksDroid APK
- **UI automation** via `uiautomator dump` + `input tap` (no Appium needed)
- Configure server IP, port, auth credentials
- Enable VPN toggle, handle system permission dialog
- Verify external IP after connection

### Detection Audit
- **45+ detection markers** across 6 categories:
  - Build properties (ro.hardware, ro.product.board, ro.build.fingerprint)
  - System files (/dev/goldfish_pipe, /dev/qemu_pipe, /system/bin/qemu-props)
  - Hardware IDs (android_id, serial, IMEI, gsf_id)
  - Runtime (CPU info, GL renderer, sensors, battery)
  - Packages (BlueStacks package visibility)
  - Network (carrier, MCC/MNC, proxy, WiFi MAC)
- **Score system**: percentage-based detection risk assessment
- Severity levels: OK / WARN / FAIL with explanations

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Web UI (FastAPI + Jinja2 + JS)              │
│              http://localhost:8899                       │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Instance Table │ Create Modal │ Proxy Modal     │   │
│  │  Audit Reports  │ Batch Proxy  │ Device Info     │   │
│  └──────────────────────────────────────────────────┘   │
└───────────────────────┬─────────────────────────────────┘
                        │ REST API
┌───────────────────────▼─────────────────────────────────┐
│                    web.py (FastAPI)                      │
│              20+ endpoints, background tasks             │
└──┬──────┬──────┬──────┬──────┬──────┬───────────────────┘
   │      │      │      │      │      │
   ▼      ▼      ▼      ▼      ▼      ▼
┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────────────┐
│Inst  ││Finger││Cloak ││Proxy ││Socks ││Device        │
│Mgr   ││print ││ing   ││Bridge││Droid ││Audit         │
│      ││      ││      ││      ││      ││              │
│create││11    ││hide  ││HTTP→ ││UI    ││45+ markers   │
│delete││device││pkgs  ││SOCKS5││auto  ││score system  │
│clone ││profiles│reset││bridge││tap   ││6 categories  │
│start ││IMEI  ││props ││      ││fill  ││              │
│stop  ││MAC   ││MAC   ││      ││VPN   ││              │
└──┬───┘└──────┘└──┬───┘└──┬───┘└──┬───┘└──────────────┘
   │               │       │       │
   ▼               ▼       ▼       ▼
┌──────────────────────────────────────────────────────┐
│              adb_manager.py (async ADB)              │
│    connect │ shell │ setprop │ install │ getprop     │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│         BlueStacks + QEMU (macOS)                    │
│  /Applications/BlueStacks.app                        │
│  ADB over TCP 127.0.0.1:555x                        │
│  bluestacks.conf (instance config)                   │
└──────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- **macOS** with [BlueStacks Air](https://www.bluestacks.com/) installed
- **Python 3.10+**
- BlueStacks master instance (`Tiramisu64`) configured and working

### Installation

```bash
git clone https://github.com/mazamaka/bluestacks-antidetect.git
cd bluestacks-antidetect

pip install -r requirements.txt

# Start the web UI
python web.py
```

Open **http://localhost:8899** in your browser.

### First Steps

1. **Create an instance** — Click "+ New Instance", set name, CPU, RAM. A unique fingerprint is auto-generated
2. **Assign a proxy** — Click the proxy icon, paste your SOCKS5 proxy, validate, apply
3. **Start the instance** — Click ▶. Proxy is auto-applied after boot via ADB
4. **Apply cloaking** — Click 🔒 to hide emulator markers
5. **Run audit** — Click 🔍 to check detection score (target: 80%+)

---

## API Reference

### Instance Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/instances` | List all instances with config |
| `POST` | `/api/instances` | Create instance(s) `{name, count, cpus, ram}` |
| `DELETE` | `/api/instances/{name}` | Delete instance (not master) |
| `POST` | `/api/instances/{name}/start` | Start instance + auto-apply proxy |
| `POST` | `/api/instances/{name}/stop` | Stop instance |
| `GET` | `/api/instances/{name}/info` | Device info via ADB |

### Proxy & Network

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/instances/{name}/apply-proxy` | Apply proxy via ADB |
| `GET` | `/api/instances/{name}/ip` | Check external IP |
| `POST` | `/api/proxy/check` | Validate single proxy `{proxy}` |
| `POST` | `/api/proxy/batch-check` | Validate multiple `{proxies: [...]}` |
| `POST` | `/api/instances/batch-proxy` | Assign proxies to instances `{proxies: {name: proxy}}` |

### Antidetect

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/instances/{name}/cloak` | Apply emulator cloaking |
| `POST` | `/api/instances/{name}/cloak-revert` | Revert cloaking |
| `GET` | `/api/instances/{name}/audit` | Run detection audit (45+ checks) |
| `GET` | `/api/fingerprint` | Generate random fingerprint |
| `POST` | `/api/instances/{name}/setup-socksdroid` | Install & configure SocksDroid VPN |
| `POST` | `/api/instances/{name}/install-apps` | Install Play Integrity Checker |

---

## How It Works

### Fingerprint Generation

Each instance gets a unique device identity generated from real device profiles:

```python
{
    "device_profile": {"brand": "Samsung", "model": "SM-G991B", "device": "o1s"},
    "android_id": "a1b2c3d4e5f6g7h8",       # 16-char hex
    "google_ad_id": "550e8400-e29b-...",       # UUID v4
    "imei": "357072111234567",                 # Valid Luhn checksum
    "serial": "RF1234567890",                  # Vendor-style prefix
    "mac_address": "a8:7d:12:xx:xx:xx",        # Real Samsung OUI
    "fb_width": 1080, "fb_height": 2220, "dpi": 420,
    "build_props": {
        "ro.product.brand": "Samsung",
        "ro.product.model": "SM-G991B",
        "ro.serialno": "RF9876543210",
        "persist.sys.timezone": "America/New_York",
    }
}
```

Fingerprints are applied at two levels:
1. **BlueStacks config** (`bluestacks.conf`) — persists across reboots
2. **ADB runtime** (`setprop` / `resetprop`) — applied after boot

### Instance Cloning (QCOW2)

New instances use **copy-on-write** QEMU disks backed by a template:

```
Template (Tiramisu64_1/data.qcow2)
    ├── Instance_2/data.qcow2  (CoW, ~50KB initial)
    ├── Instance_3/data.qcow2  (CoW, ~50KB initial)
    └── Instance_4/data.qcow2  (CoW, ~50KB initial)
```

This makes instance creation nearly instant — only modified blocks are written to the new disk.

### Proxy Bridge

Android can't connect to SOCKS5 directly (without VPN). The bridge solves this:

```
Android App                    Host Machine               Remote Proxy
    │                              │                          │
    ├── HTTP request ──────►  Local HTTP Server          │
    │   (10.0.2.2:18800)           │                          │
    │                         SOCKS5 connect ────────►  proxy.host:1080
    │                              │                          │
    │   ◄────────── relay ─────────┼──────── relay ──────────►│
```

Each instance gets its own bridge process on a dedicated port (18800, 18801, ...).

### Cloaking Process

Cloaking hides emulator markers without modifying system partitions:

| Step | Method | What it does |
|------|--------|-------------|
| Security props | `resetprop` (Magisk) | `ro.secure=1`, `ro.debuggable=0`, `ro.build.type=user` |
| Device identity | `resetprop` + `setprop` | Chipname, board platform matching device profile |
| Package hiding | `pm hide` / `pm disable-user` | Hides 6 BlueStacks packages (keeps launcher) |
| WiFi MAC | `ip link set` (root) | Sets MAC with real vendor OUI prefix |
| Carrier info | `setprop` | `gsm.operator.alpha="T-Mobile"` |

All changes are **reversible** — props reset on reboot, packages can be unhidden.

### Detection Audit

The audit checks 45+ markers across 6 categories:

```
Category          Examples                              Bad Values
─────────────────────────────────────────────────────────────────
Build Props       ro.hardware, ro.product.board         ranchu, goldfish, generic
System Files      /dev/goldfish_pipe, /dev/qemu_pipe    (exists = fail)
Hardware IDs      android_id, serial, IMEI              empty, "unknown", "123456"
Runtime           /proc/cpuinfo, GL renderer, sensors   qemu, kvm, swiftshader
Packages          BlueStacks packages visible           com.bluestacks.* present
Network           carrier, MCC/MNC, WiFi MAC            empty, 10.0.2.2 proxy
```

Score = (passed / total) × 100. Target: **80%+** after cloaking.

---

## Project Structure

```
bluestacks-antidetect/
├── web.py                 # FastAPI app — 20+ REST endpoints, web UI
├── config.py              # Constants, device profiles, BlueStacks paths
├── instance_manager.py    # Instance lifecycle (create/delete/clone/start/stop)
├── bs_conf.py             # BlueStacks config parser (bluestacks.conf)
├── fingerprint.py         # Device fingerprint generation (IMEI, MAC, IDs)
├── cloaking.py            # Emulator marker hiding (props, packages, MAC)
├── adb_manager.py         # Async ADB communication layer
├── proxy_bridge.py        # HTTP → SOCKS5 local bridge (multiprocessing)
├── proxy_manager.py       # Proxy validation, assignment, storage
├── socksdroid.py          # SocksDroid VPN UI automation
├── device_audit.py        # 45+ marker detection audit with scoring
├── templates/
│   └── index.html         # Web UI (dark theme, modals, real-time actions)
├── apks/
│   ├── magisk.apk         # Root for system prop modification
│   ├── socksdroid.apk     # SOCKS5 VPN app
│   ├── integrity-check/   # Play Integrity Checker (split APKs)
│   └── play-console/      # Google Play Console (split APKs)
├── requirements.txt       # Python dependencies
└── .gitignore
```

---

## Device Profiles

| Brand | Models | Chipset |
|-------|--------|---------|
| Samsung | Galaxy S21, S22 Ultra, S20 FE, A53, A54 | Exynos 2100/2200/1280/1380 |
| Google | Pixel 7, Pixel 8 Pro, Pixel 6a | Tensor G2/G3/GS201 |
| Xiaomi | Redmi Note 11 Pro | Snapdragon 680 |
| OnePlus | OnePlus 11 | Snapdragon 8 Gen 2 |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.10+, FastAPI, Pydantic |
| Web UI | Jinja2, vanilla JS, dark theme |
| ADB | Async subprocess (BlueStacks ADB / system ADB) |
| Proxy | Multiprocessing HTTP→SOCKS5 bridge, httpx for validation |
| Disk | QEMU QCOW2 copy-on-write |
| Logging | Loguru |
| Platform | macOS (BlueStacks Air) |

---

## Limitations

- **macOS only** — BlueStacks Air paths are hardcoded for macOS
- **Magisk required** — `resetprop` needs Magisk installed in the instance for full cloaking
- **BlueStacks Air** — tested with BlueStacks Air (Tiramisu/Android 13), other versions may differ
- Credentials stored in `profiles.json` (not encrypted)

---

## License

MIT
