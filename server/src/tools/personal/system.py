"""Local system-inspection and lightweight desktop utility tools."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any


class SystemTools:
    def system_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "cwd": str(Path.cwd()),
        }
        try:
            import psutil

            vm = psutil.virtual_memory()
            disk = psutil.disk_usage(str(Path.home()))
            info.update(
                {
                    "memory": {
                        "total": vm.total,
                        "available": vm.available,
                        "percent": vm.percent,
                    },
                    "disk": {
                        "total": disk.total,
                        "free": disk.free,
                        "percent": disk.percent,
                    },
                    "boot_time": psutil.boot_time(),
                }
            )
        except ImportError:
            pass

        nvidia = shutil.which("nvidia-smi")
        if nvidia:
            try:
                result = subprocess.run(
                    [nvidia, "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.stdout.strip():
                    info["gpu"] = result.stdout.strip().splitlines()
            except Exception:
                pass
        return info

    @staticmethod
    def which(binary: str) -> dict[str, Any]:
        path = shutil.which(binary)
        return {"binary": binary, "found": path is not None, "path": path}

    @staticmethod
    def port_check(host: str, port: int, timeout_seconds: float = 1.5) -> dict[str, Any]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_seconds)
            try:
                code = sock.connect_ex((host, int(port)))
            except OSError as exc:
                return {"host": host, "port": int(port), "open": False, "error": str(exc)}
        return {"host": host, "port": int(port), "open": code == 0}

    @staticmethod
    def network_info() -> dict[str, Any]:
        result: dict[str, Any] = {"hostname": socket.gethostname(), "addresses": []}
        try:
            result["addresses"] = sorted(
                {item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None)}
            )
        except OSError:
            pass
        try:
            import psutil

            interfaces: dict[str, list[dict[str, Any]]] = {}
            for name, addresses in psutil.net_if_addrs().items():
                interfaces[name] = [
                    {"family": str(address.family), "address": address.address, "netmask": address.netmask}
                    for address in addresses
                ]
            result["interfaces"] = interfaces
        except ImportError:
            pass
        return result

    @staticmethod
    def clipboard_read() -> str:
        try:
            import pyperclip
        except ImportError as exc:
            raise RuntimeError("pyperclip is required for clipboard tools") from exc
        return str(pyperclip.paste())

    @staticmethod
    def clipboard_write(text: str) -> str:
        try:
            import pyperclip
        except ImportError as exc:
            raise RuntimeError("pyperclip is required for clipboard tools") from exc
        pyperclip.copy(text)
        return "Clipboard updated"

    @staticmethod
    def wake_on_lan(mac_address: str, *, broadcast: str = "255.255.255.255", port: int = 9) -> str:
        clean = mac_address.replace(":", "").replace("-", "").strip()
        if len(clean) != 12:
            raise ValueError("MAC address must contain 12 hex digits")
        try:
            mac = bytes.fromhex(clean)
        except ValueError as exc:
            raise ValueError("Invalid MAC address") from exc
        packet = b"\xff" * 6 + mac * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (broadcast, int(port)))
        return f"Wake-on-LAN packet sent to {mac_address}"
