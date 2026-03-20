"""BlueStacks Air configuration paths and constants.

All paths are macOS-specific for BlueStacks Air.
"""

from pathlib import Path

# BlueStacks paths
BS_DATA_DIR = Path("/Users/Shared/Library/Application Support/BlueStacks")
BS_CONF_FILE = BS_DATA_DIR / "bluestacks.conf"
BS_ENGINE_DIR = BS_DATA_DIR / "Engine"

# BlueStacks binaries
BS_APP = Path("/Applications/BlueStacks.app")
BS_MIM_APP = Path("/Applications/BlueStacksMIM.app")
BS_ADB = BS_APP / "Contents/MacOS/hd-adb"
BS_QEMU_IMG = BS_APP / "Contents/MacOS/qemu-img"
BS_MAIN = BS_APP / "Contents/MacOS/BlueStacks"

# System ADB fallback
SYSTEM_ADB = Path("/opt/homebrew/bin/adb")

# Base instance name (master)
MASTER_INSTANCE = "Tiramisu64"

# Device profiles for fingerprint spoofing
DEVICE_PROFILES = [
    {
        "brand": "Samsung",
        "manufacturer": "samsung",
        "model": "SM-G991B",
        "device": "o1s",
        "product": "o1sxeea",
        "name": "Galaxy S21",
    },
    {
        "brand": "Samsung",
        "manufacturer": "samsung",
        "model": "SM-S908B",
        "device": "b0s",
        "product": "b0sxeea",
        "name": "Galaxy S22 Ultra",
    },
    {
        "brand": "Samsung",
        "manufacturer": "samsung",
        "model": "SM-A536B",
        "device": "a53x",
        "product": "a53xeea",
        "name": "Galaxy A53",
    },
    {
        "brand": "Google",
        "manufacturer": "Google",
        "model": "Pixel 7",
        "device": "panther",
        "product": "panther",
        "name": "Pixel 7",
    },
    {
        "brand": "Google",
        "manufacturer": "Google",
        "model": "Pixel 8 Pro",
        "device": "husky",
        "product": "husky",
        "name": "Pixel 8 Pro",
    },
    {
        "brand": "Xiaomi",
        "manufacturer": "Xiaomi",
        "model": "2201117TG",
        "device": "veux",
        "product": "veux_global",
        "name": "Redmi Note 11 Pro",
    },
    {
        "brand": "OnePlus",
        "manufacturer": "OnePlus",
        "model": "CPH2449",
        "device": "OP5958L1",
        "product": "OP5958L1",
        "name": "OnePlus 11",
    },
    {
        "brand": "Samsung",
        "manufacturer": "samsung",
        "model": "SM-G780G",
        "device": "r8q",
        "product": "r8qxeea",
        "name": "Galaxy S20 FE",
    },
    {
        "brand": "Samsung",
        "manufacturer": "samsung",
        "model": "SM-A546B",
        "device": "a54x",
        "product": "a54xeea",
        "name": "Galaxy A54",
    },
    {
        "brand": "Google",
        "manufacturer": "Google",
        "model": "Pixel 6a",
        "device": "bluejay",
        "product": "bluejay",
        "name": "Pixel 6a",
    },
]

# Screen resolutions (width x height x dpi)
SCREEN_PROFILES = [
    (1080, 1920, 420),   # FHD standard
    (1080, 2340, 420),   # FHD+ 19.5:9
    (1080, 2400, 400),   # FHD+ 20:9
    (1440, 2560, 560),   # QHD
    (1080, 1920, 480),   # FHD high dpi
    (720, 1280, 320),    # HD
    (1080, 2220, 420),   # FHD+ 18.5:9
]
