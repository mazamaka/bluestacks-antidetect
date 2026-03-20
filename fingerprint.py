"""Fingerprint generation for BlueStacks instances."""

import random
import uuid
import hashlib
from loguru import logger

from config import DEVICE_PROFILES, SCREEN_PROFILES


def generate_android_id() -> str:
    """Generate random 16-char hex Android ID."""
    return hashlib.md5(uuid.uuid4().bytes).hexdigest()[:16]


def generate_google_ad_id() -> str:
    """Generate random Google Advertising ID (UUID v4)."""
    return str(uuid.uuid4())


def generate_imei() -> str:
    """Generate valid IMEI (15 digits with Luhn check)."""
    # TAC (8 digits) + serial (6 digits) + check (1 digit)
    tac_list = [
        "35332509", "86769702", "35451406", "35290611",
        "35524006", "35418900", "35856110", "86415603",
    ]
    tac = random.choice(tac_list)
    serial = f"{random.randint(0, 999999):06d}"
    partial = tac + serial

    # Luhn check digit
    total = 0
    for i, ch in enumerate(partial):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return partial + str(check)


def generate_mac_address() -> str:
    """Generate random MAC address (locally administered, unicast)."""
    octets = [random.randint(0, 255) for _ in range(6)]
    octets[0] = (octets[0] & 0xFC) | 0x02  # locally administered, unicast
    return ":".join(f"{o:02x}" for o in octets)


def generate_serial() -> str:
    """Generate random device serial number."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    prefix = random.choice(["RF", "ZX", "R5", "HQ", "RZ", "RM", "R9"])
    return prefix + "".join(random.choices(chars, k=random.randint(9, 11)))


def generate_fingerprint(seed: int | None = None) -> dict[str, object]:
    """Generate a complete device fingerprint.

    Returns dict with all values needed to spoof a unique device.
    """
    if seed is not None:
        random.seed(seed)

    device = random.choice(DEVICE_PROFILES)
    screen = random.choice(SCREEN_PROFILES)

    fp = {
        # Device identity
        "device_profile": device,
        "android_id": generate_android_id(),
        "google_ad_id": generate_google_ad_id(),
        "imei": generate_imei(),
        "serial": generate_serial(),
        "mac_address": generate_mac_address(),

        # Screen
        "fb_width": screen[0],
        "fb_height": screen[1],
        "dpi": screen[2],

        # Build props (applied via ADB)
        "build_props": {
            "ro.product.brand": device["brand"],
            "ro.product.manufacturer": device["manufacturer"],
            "ro.product.model": device["model"],
            "ro.product.device": device["device"],
            "ro.product.name": device["product"],
            "ro.serialno": generate_serial(),
            "persist.sys.timezone": random.choice([
                "America/New_York", "America/Chicago", "America/Los_Angeles",
                "Europe/London", "Europe/Berlin", "Europe/Paris",
                "Asia/Tokyo", "Asia/Singapore",
            ]),
            "persist.sys.language": "en",
            "persist.sys.country": random.choice(["US", "GB", "DE", "FR", "JP", "AU", "CA"]),
        },
    }

    logger.debug("Generated fingerprint: {} {} android_id={}",
                 device["brand"], device["model"], fp["android_id"])
    return fp
