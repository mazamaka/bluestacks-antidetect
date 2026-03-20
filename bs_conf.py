"""Parser and writer for bluestacks.conf format."""

import re
import shutil
from pathlib import Path
from datetime import datetime
from loguru import logger

from config import BS_CONF_FILE


def parse_conf(path: Path = BS_CONF_FILE) -> dict[str, str]:
    """Parse bluestacks.conf into a dict."""
    result = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r'^([^=]+)="(.*)"$', line)
        if match:
            result[match.group(1)] = match.group(2)
        else:
            logger.warning("Skipping unparseable line: {}", line)
    logger.debug("Parsed {} keys from {}", len(result), path)
    return result


def write_conf(data: dict[str, str], path: Path = BS_CONF_FILE):
    """Write dict back to bluestacks.conf format."""
    # Backup first
    backup = path.with_suffix(f".conf.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(path, backup)
    logger.info("Backup created: {}", backup)

    lines = []
    for key in sorted(data.keys()):
        lines.append(f'{key}="{data[key]}"')

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Written {} keys to {}", len(data), path)


def get_instance_keys(data: dict[str, str], instance_name: str) -> dict[str, str]:
    """Get all keys for a specific instance."""
    prefix = f"bst.instance.{instance_name}."
    return {k: v for k, v in data.items() if k.startswith(prefix)}


def get_instances(data: dict[str, str]) -> list[str]:
    """Get list of instance names from installed_images."""
    images = data.get("bst.installed_images", "")
    return [x.strip() for x in images.split(",") if x.strip()]


def set_instance_value(data: dict[str, str], instance_name: str, key: str, value: str):
    """Set a specific value for an instance."""
    full_key = f"bst.instance.{instance_name}.{key}"
    data[full_key] = value


def clone_instance_config(data: dict[str, str], source: str, target: str):
    """Clone all config keys from source instance to target."""
    src_prefix = f"bst.instance.{source}."
    for key, value in list(data.items()):
        if key.startswith(src_prefix):
            new_key = key.replace(src_prefix, f"bst.instance.{target}.", 1)
            data[new_key] = value

    # Add to installed_images
    images = get_instances(data)
    if target not in images:
        images.append(target)
        data["bst.installed_images"] = ",".join(images)

    logger.info("Cloned config {} -> {}", source, target)
