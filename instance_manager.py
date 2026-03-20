"""BlueStacks instance lifecycle manager."""

import shutil
import subprocess
from pathlib import Path
from loguru import logger

from config import (
    BS_APP, BS_ENGINE_DIR, BS_QEMU_IMG, MASTER_INSTANCE,
)
from bs_conf import (
    parse_conf, write_conf, clone_instance_config,
    set_instance_value, get_instances, get_instance_keys,
)
from fingerprint import generate_fingerprint
from adb_manager import connect, apply_build_props, set_android_id

# Clean factory data image (no accounts, no apps)
CLEAN_DATA_IMG = BS_APP / "Contents/img/data-org.qcow2"


class InstanceManager:
    """Manages BlueStacks instances: create, configure, start, stop."""

    def __init__(self) -> None:
        self.conf = parse_conf()
        self._base_adb_port = 5555

    def reload_conf(self) -> None:
        """Reload bluestacks.conf from disk."""
        self.conf = parse_conf()

    def list_instances(self) -> list[dict]:
        """List all instances with their config.

        Returns:
            List of instance info dicts.
        """
        instances = get_instances(self.conf)
        result = []
        for name in instances:
            keys = get_instance_keys(self.conf, name)
            prefix = f"bst.instance.{name}."
            result.append({
                "name": name,
                "adb_port": int(keys.get(f"{prefix}adb_port", "0")),
                "display_name": keys.get(f"{prefix}display_name", name),
                "brand": keys.get(f"{prefix}device_custom_brand", ""),
                "model": keys.get(f"{prefix}device_custom_model", ""),
                "android_id": keys.get(f"{prefix}android_id", ""),
                "resolution": "{}x{}".format(
                    keys.get(f"{prefix}fb_width", "?"),
                    keys.get(f"{prefix}fb_height", "?"),
                ),
                "dpi": keys.get(f"{prefix}dpi", "?"),
                "cpus": keys.get(f"{prefix}cpus", "?"),
                "ram": keys.get(f"{prefix}ram", "?"),
            })
        return result

    def _next_adb_port(self) -> int:
        """Find next available ADB port."""
        instances = get_instances(self.conf)
        used_ports: set[int] = set()
        for name in instances:
            port_key = f"bst.instance.{name}.adb_port"
            if port_key in self.conf:
                used_ports.add(int(self.conf[port_key]))
        port = self._base_adb_port
        while port in used_ports:
            port += 10
        return port

    def _next_instance_name(self) -> str:
        """Generate next valid BlueStacks instance name.

        BlueStacks requires: Tiramisu64, Tiramisu64_1, Tiramisu64_2, etc.
        """
        vm_id = int(self.conf.get("bst.next_vm_id", "1"))
        name = f"Tiramisu64_{vm_id}"
        instances = get_instances(self.conf)
        while name in instances:
            vm_id += 1
            name = f"Tiramisu64_{vm_id}"
        self.conf["bst.next_vm_id"] = str(vm_id + 1)
        return name

    def create_instance(
        self,
        display_name: str,
        source: str = MASTER_INSTANCE,
        fingerprint: dict | None = None,
        cpus: int = 4,
        ram: int = 4096,
    ) -> dict:
        """Create a new instance from clean factory image.

        Args:
            display_name: Human-readable name (shown in UI).
            source: Source instance for config cloning.
            fingerprint: Device fingerprint dict. Auto-generated if None.
            cpus: Number of CPU cores.
            ram: RAM in MB.

        Returns:
            Instance info dict.

        Raises:
            FileNotFoundError: If source instance directory not found.
        """
        source_dir = BS_ENGINE_DIR / source
        if not source_dir.exists():
            raise FileNotFoundError(f"Source instance '{source}' not found at {source_dir}")

        name = self._next_instance_name()

        if fingerprint is None:
            fingerprint = generate_fingerprint()

        # 1. Create instance directory
        target_dir = BS_ENGINE_DIR / name
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created instance dir: {}", target_dir)

        # 2. Create disk from clean factory image (no accounts/data)
        target_disk = target_dir / "data.qcow2"
        if target_disk.exists():
            target_disk.unlink()

        if CLEAN_DATA_IMG.exists() and BS_QEMU_IMG.exists():
            logger.info("Creating clean disk from factory image...")
            subprocess.run(
                [
                    str(BS_QEMU_IMG), "create", "-f", "qcow2",
                    "-b", str(CLEAN_DATA_IMG), "-F", "qcow2",
                    str(target_disk),
                ],
                check=True, capture_output=True,
            )
            logger.info("Clean disk created (backing: data-org.qcow2)")
        else:
            source_disk = source_dir / "data.qcow2"
            logger.warning("Factory image not found, copying master disk (includes accounts!)")
            shutil.copy2(source_disk, target_disk)

        # 3. Clone config from source
        clone_instance_config(self.conf, source, name)

        # 4. Apply fingerprint + settings
        adb_port = self._next_adb_port()
        fp = fingerprint

        set_instance_value(self.conf, name, "adb_port", str(adb_port))
        set_instance_value(self.conf, name, "status.adb_port", str(adb_port))
        set_instance_value(self.conf, name, "display_name", display_name)
        set_instance_value(self.conf, name, "android_id", fp["android_id"])
        set_instance_value(self.conf, name, "android_google_ad_id", fp["google_ad_id"])
        set_instance_value(self.conf, name, "device_custom_brand", fp["device_profile"]["brand"])
        set_instance_value(self.conf, name, "device_custom_manufacturer", fp["device_profile"]["manufacturer"])
        set_instance_value(self.conf, name, "device_custom_model", fp["device_profile"]["model"])

        # Ensure portrait orientation (width < height)
        fb_w, fb_h = fp["fb_width"], fp["fb_height"]
        if fb_w > fb_h:
            fb_w, fb_h = fb_h, fb_w

        set_instance_value(self.conf, name, "fb_width", str(fb_w))
        set_instance_value(self.conf, name, "fb_height", str(fb_h))
        set_instance_value(self.conf, name, "dpi", str(fp["dpi"]))
        set_instance_value(self.conf, name, "cpus", str(cpus))
        set_instance_value(self.conf, name, "ram", str(ram))

        # Clean slate: no google accounts from master, fresh boot
        set_instance_value(self.conf, name, "google_account_logins", "")
        set_instance_value(self.conf, name, "first_boot", "1")
        set_instance_value(self.conf, name, "enable_root_access", "0")
        set_instance_value(self.conf, name, "gl_win_height", "900")

        # 5. Save config
        write_conf(self.conf)

        info = {
            "name": name,
            "display_name": display_name,
            "adb_port": adb_port,
            "device": f"{fp['device_profile']['brand']} {fp['device_profile']['model']}",
            "android_id": fp["android_id"],
            "resolution": f"{fp['fb_width']}x{fp['fb_height']}",
            "dpi": fp["dpi"],
            "fingerprint": fp,
        }
        logger.info(
            "Instance '{}' ({}) created: {} port={}",
            name, display_name, info["device"], adb_port,
        )
        return info

    def delete_instance(self, name: str) -> None:
        """Delete an instance (cannot delete master).

        Args:
            name: Instance name (e.g. Tiramisu64_3).

        Raises:
            ValueError: If trying to delete master or non-existent instance.
        """
        if name == MASTER_INSTANCE:
            raise ValueError("Cannot delete master instance")

        instances = get_instances(self.conf)
        if name not in instances:
            raise ValueError(f"Instance '{name}' not found")

        target_dir = BS_ENGINE_DIR / name
        if target_dir.exists():
            shutil.rmtree(target_dir)
            logger.info("Deleted instance dir: {}", target_dir)

        # Remove from config
        prefix = f"bst.instance.{name}."
        keys_to_remove = [k for k in self.conf if k.startswith(prefix)]
        for k in keys_to_remove:
            del self.conf[k]

        instances.remove(name)
        self.conf["bst.installed_images"] = ",".join(instances)
        write_conf(self.conf)
        logger.info("Instance '{}' deleted", name)

    def batch_create(
        self,
        count: int,
        name_prefix: str = "Profile",
        cpus: int = 4,
        ram: int = 4096,
    ) -> list[dict]:
        """Create multiple instances with unique fingerprints."""
        results: list[dict] = []
        for i in range(1, count + 1):
            display_name = f"{name_prefix} {i}" if count > 1 else name_prefix
            try:
                info = self.create_instance(display_name, cpus=cpus, ram=ram)
                results.append(info)
            except FileNotFoundError as e:
                logger.error("Cannot create instance: {}", e)
                break
            except subprocess.CalledProcessError as e:
                logger.warning("Disk creation failed for '{}': {}", display_name, e)
        return results

    async def apply_fingerprint_via_adb(self, name: str, fingerprint: dict) -> None:
        """Apply build props and android_id via ADB after instance is running.

        Args:
            name: Instance name.
            fingerprint: Fingerprint dict from generate_fingerprint().

        Raises:
            ValueError: If no ADB port configured.
            ConnectionError: If ADB connection fails.
        """
        adb_port = int(self.conf.get(f"bst.instance.{name}.adb_port", "0"))
        if not adb_port:
            raise ValueError(f"No ADB port for instance '{name}'")

        if not await connect(adb_port):
            raise ConnectionError(f"Cannot connect to instance '{name}' on port {adb_port}")

        if "build_props" in fingerprint:
            await apply_build_props(adb_port, fingerprint["build_props"])

        if "android_id" in fingerprint:
            await set_android_id(adb_port, fingerprint["android_id"])

        logger.info("Fingerprint applied via ADB to '{}' (port {})", name, adb_port)
