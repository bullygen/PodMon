import os
from pathlib import Path


class HostMetrics:
    def __init__(self, root="/host/proc"):
        self.root = Path(root)
        self.previous = None

    def collect(self):
        out = {"hostname": None, "uptime_seconds": None, "cpu_percent": None, "load_average": None, "memory_used": None, "memory_total": None, "swap_used": None, "swap_total": None, "disk_used": None, "disk_total": None, "scope": "host metrics require optional read-only mounts"}
        try:
            out["hostname"] = Path("/host/hostname").read_text().strip()
        except OSError:
            pass
        try:
            stat = self.root.joinpath("stat").read_text().splitlines()[0].split()[1:]
            values = [int(v) for v in stat]
            total, idle = sum(values), values[3] + values[4]
            if self.previous:
                dt, di = total - self.previous[0], idle - self.previous[1]
                if dt > 0:
                    out["cpu_percent"] = round(100 * (dt - di) / dt, 1)
            self.previous = total, idle
            out["uptime_seconds"] = int(float(self.root.joinpath("uptime").read_text().split()[0]))
            out["load_average"] = [float(v) for v in self.root.joinpath("loadavg").read_text().split()[:3]]
            mem = {}
            for line in self.root.joinpath("meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                mem[key] = int(value.strip().split()[0]) * 1024
            out["memory_total"] = mem.get("MemTotal")
            out["memory_used"] = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
            out["swap_total"] = mem.get("SwapTotal")
            out["swap_used"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
            out["scope"] = "read-only host proc files"
        except (OSError, ValueError, IndexError):
            pass
        if out["hostname"]:
            try:
                disk = os.statvfs("/host/hostname")
                out["disk_total"] = disk.f_blocks * disk.f_frsize
                out["disk_used"] = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
            except OSError:
                pass
        return out
