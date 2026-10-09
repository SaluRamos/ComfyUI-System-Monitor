import csv
import ctypes
import json
import uuid

import psutil
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def _memory_bytes(value):
    try:
        mib = float(value.strip())
    except ValueError:
        return None
    return int(mib * 1024 ** 2) if math.isfinite(mib) and mib >= 0 else None


def _utilization(value):
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return min(100.0, max(0.0, percent)) if math.isfinite(percent) and percent >= 0 else None


def parse_nvidia_csv(text):
    gpus = []
    for row in csv.reader(text.splitlines(), skipinitialspace=True):
        if len(row) not in (5, 6):
            continue
        index, uuid, name, used, total = (value.strip() for value in row[:5])
        gpus.append({"id": uuid, "index": index, "name": name, "vendor": "NVIDIA",
                     "used_bytes": _memory_bytes(used), "total_bytes": _memory_bytes(total),
                     "utilization_percent": _utilization(row[5]) if len(row) == 6 else None})
    return gpus



class AdapterDesc(ctypes.Structure):
    _fields_ = [("description", ctypes.c_wchar * 128),
                ("vendor", ctypes.c_uint32), ("device", ctypes.c_uint32),
                ("subsystem", ctypes.c_uint32), ("revision", ctypes.c_uint32),
                ("dedicated", ctypes.c_size_t), ("system", ctypes.c_size_t),
                ("shared", ctypes.c_size_t), ("luid_low", ctypes.c_uint32),
                ("luid_high", ctypes.c_uint32), ("flags", ctypes.c_uint32)]


def _com_method(pointer, index, result_type, *args):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *args)(table[index])


def windows_adapters():
    factory = ctypes.c_void_p()
    iid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    create = ctypes.WinDLL("dxgi").CreateDXGIFactory1
    create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_int32
    if create(ctypes.byref(iid), ctypes.byref(factory)) < 0:
        raise OSError("CreateDXGIFactory1 failed")
    gpus = []
    try:
        enum = _com_method(factory, 12, ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            result = enum(factory, index, ctypes.byref(adapter))
            if result == -2005270526:  # DXGI_ERROR_NOT_FOUND
                break
            if result < 0:
                raise OSError("EnumAdapters1 failed")
            try:
                desc = AdapterDesc()
                get_desc = _com_method(adapter, 10, ctypes.c_int32, ctypes.POINTER(AdapterDesc))
                if get_desc(adapter, ctypes.byref(desc)) < 0:
                    raise OSError("GetDesc1 failed")
                if not desc.flags & 2:  # Exclude software adapters.
                    luid = f"luid_0x{desc.luid_high:08x}_0x{desc.luid_low:08x}"
                    gpus.append({"id": luid, "index": str(index), "name": desc.description,
                                 "vendor": {0x8086: "Intel", 0x1002: "AMD", 0x10de: "NVIDIA"}.get(desc.vendor, "GPU"),
                                 "used_bytes": None, "total_bytes": desc.dedicated, "utilization_percent": None})
            finally:
                _com_method(adapter, 2, ctypes.c_uint32)(adapter)
            index += 1
    finally:
        _com_method(factory, 2, ctypes.c_uint32)(factory)
    return gpus


def apply_windows_usage(gpus, counters):
    for gpu in gpus:
        matches = [counter for counter in counters
                   if counter["Name"].lower().startswith(gpu["id"] + "_phys_")]
        if matches and all(counter.get("DedicatedUsage") is not None for counter in matches):
            gpu["used_bytes"] = sum(int(counter["DedicatedUsage"]) for counter in matches)


def apply_windows_engines(gpus, counters):
    for gpu in gpus:
        engines = {}
        for counter in counters:
            name = counter["Name"].lower()
            match = re.search(r"luid_0x[0-9a-f]+_0x[0-9a-f]+_phys_\d+_eng_\d+", name)
            value = _utilization(counter.get("UtilizationPercentage"))
            if match and match.group().startswith(gpu["id"] + "_phys_") and value is not None:
                engine = match.group()
                engines[engine] = engines.get(engine, 0.0) + value
        gpu["utilization_percent"] = min(100.0, max(engines.values())) if engines else None


def collect_windows_gpus():
    errors = []
    try:
        gpus = windows_adapters()
    except OSError:
        return [], ["Não foi possível identificar as GPUs pelo DXGI."]
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; "
             "$memory=@(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory | Select-Object Name,DedicatedUsage); "
             "$engines=@(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction SilentlyContinue | Select-Object Name,UtilizationPercentage); "
             "@{memory=$memory; engines=$engines} | ConvertTo-Json -Compress -Depth 3"],
            capture_output=True, text=True, timeout=4, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise OSError("GPU counters unavailable")
        counters = json.loads(result.stdout or "[]")
        apply_windows_usage(gpus, counters["memory"])
        apply_windows_engines(gpus, counters["engines"])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        errors.append("Não foi possível consultar os contadores de GPU do Windows.")
    return gpus, errors

def collect_gpus():
    cpu = {"utilization_percent": psutil.cpu_percent(interval=0.1)}
    ram = psutil.virtual_memory()
    memory = {"used_bytes": ram.total - ram.available, "total_bytes": ram.total}
    if IS_WINDOWS:
        gpus, errors = collect_windows_gpus()
        if not gpus and not errors:
            errors.append("Nenhuma GPU detectada pelo Windows.")
        return {"gpus": gpus, "ram": memory, "cpu": cpu, "errors": errors, "timestamp": time.time()}
    gpus, errors = [], []
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            result = subprocess.run(
                [executable, "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=4,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0:
                gpus.extend(parse_nvidia_csv(result.stdout))
                if result.stdout.strip() and not gpus:
                    errors.append("Não foi possível interpretar a leitura do driver NVIDIA.")
            else:
                errors.append("O driver NVIDIA não respondeu à consulta de VRAM.")
        except (OSError, subprocess.TimeoutExpired):
            errors.append("Não foi possível consultar nvidia-smi; verifique o driver NVIDIA.")

    # Linux exposes dedicated memory counters for supported DRM drivers.
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue
        device = card / "device"
        if not (device / "mem_info_vram_total").is_file():
            continue
        try:
            vendor = (device / "vendor").read_text().strip()
            if vendor == "0x10de":
                continue
            total = int((device / "mem_info_vram_total").read_text().strip())
            used = int((device / "mem_info_vram_used").read_text().strip())
            label = {"0x1002": "AMD", "0x8086": "Intel"}.get(vendor, "GPU")
            product = device / "product_name"
            name = product.read_text().strip() if product.is_file() else f"{label} {card.name}"
            gpus.append({"id": str(device.resolve()), "index": card.name, "name": name, "vendor": label,
                         "used_bytes": max(0, used), "total_bytes": max(0, total),
                         "utilization_percent": None})
            busy = device / "gpu_busy_percent"
            if busy.is_file():
                gpus[-1]["utilization_percent"] = _utilization(busy.read_text().strip())
        except (OSError, ValueError):
            errors.append(f"Não foi possível consultar a VRAM de {card.name}.")
    if not gpus and not errors:
        errors.append("Nenhuma GPU com leitura de VRAM disponível. NVIDIA requer nvidia-smi no PATH; AMD requer contadores DRM no Linux.")
    return {"gpus": gpus, "ram": memory, "cpu": cpu, "errors": errors, "timestamp": time.time()}


class GPUMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = None
        self._expires = 0.0

    def snapshot(self):
        with self._lock:
            if self._snapshot is None or time.monotonic() >= self._expires:
                self._snapshot = collect_gpus()
                self._expires = time.monotonic() + 0.1
            return self._snapshot

