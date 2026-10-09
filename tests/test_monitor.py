import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gpu_monitor_test", ROOT / "monitor.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

CSV = "0, GPU-a, NVIDIA RTX 3060, 2048, 12288\n1, GPU-b, NVIDIA RTX 4090, 4096, 24576\n"


class MonitorTests(unittest.TestCase):
    def setUp(self):
        platform = patch.object(monitor, "IS_WINDOWS", False)
        platform.start()
        self.addCleanup(platform.stop)

    def test_windows_intel_amd_and_nvidia_memory(self):
        gpus = [{"id": f"luid_0x00000000_0x0000000{i}", "index": str(i),
                 "name": vendor + " Test", "vendor": vendor,
                 "used_bytes": None, "total_bytes": 32 * 1024 ** 3}
                for i, vendor in enumerate(["Intel", "AMD", "NVIDIA"])]
        counters = [{"Name": gpu["id"].upper() + "_phys_0", "DedicatedUsage": (i + 1) * 1024 ** 3}
                    for i, gpu in enumerate(gpus)]
        counters.append({"Name": "luid_0x00000000_0x99999999_phys_0", "DedicatedUsage": 123})
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(counters))
        with patch.object(monitor, "IS_WINDOWS", True), patch.object(monitor, "windows_adapters", return_value=gpus), \
                patch.object(monitor.subprocess, "run", return_value=result):
            data = monitor.collect_gpus()
        self.assertEqual([gpu["vendor"] for gpu in data["gpus"]], ["Intel", "AMD", "NVIDIA"])
        self.assertEqual([gpu["used_bytes"] for gpu in data["gpus"]], [1024 ** 3, 2 * 1024 ** 3, 3 * 1024 ** 3])
        self.assertEqual(data["gpus"][0]["total_bytes"], 32 * 1024 ** 3)
        self.assertEqual(data["errors"], [])
        self.assertGreater(data["ram"]["total_bytes"], 0)

    def test_windows_missing_counters_preserves_total(self):
        gpus = [{"id": "luid_0x00000000_0x00000001", "used_bytes": None, "total_bytes": 32 * 1024 ** 3}]
        with patch.object(monitor, "windows_adapters", return_value=gpus), \
                patch.object(monitor.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell", 4)):
            actual, errors = monitor.collect_windows_gpus()
        self.assertIsNone(actual[0]["used_bytes"])
        self.assertEqual(actual[0]["total_bytes"], 32 * 1024 ** 3)
        self.assertTrue(errors)

    def test_ram_uses_available_memory(self):
        ram = types.SimpleNamespace(total=32 * 1024 ** 3, available=20 * 1024 ** 3)
        with patch.object(monitor.psutil, "virtual_memory", return_value=ram), \
                patch.object(monitor.shutil, "which", return_value=None), patch.object(monitor.Path, "glob", return_value=[]):
            data = monitor.collect_gpus()
        self.assertEqual(data["ram"], {"used_bytes": 12 * 1024 ** 3, "total_bytes": 32 * 1024 ** 3})

    def test_multiple_gpus_and_units(self):
        gpus = monitor.parse_nvidia_csv(CSV)
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0]["used_bytes"], 2 * 1024 ** 3)
        self.assertEqual(gpus[1]["total_bytes"], 24 * 1024 ** 3)
        self.assertNotEqual(gpus[0]["id"], gpus[1]["id"])

    def test_unavailable_metrics_and_quoted_names(self):
        gpus = monitor.parse_nvidia_csv('0, GPU-a, "NVIDIA, Test", [N/A], 12288\n1, GPU-b, Test, NaN, -1\n')
        self.assertEqual(gpus[0]["name"], "NVIDIA, Test")
        self.assertIsNone(gpus[0]["used_bytes"])
        self.assertIsNone(gpus[1]["used_bytes"])
        self.assertIsNone(gpus[1]["total_bytes"])
        self.assertEqual(monitor.parse_nvidia_csv("invalid\n"), [])

    @patch.object(monitor.Path, "glob", return_value=[])
    @patch.object(monitor.shutil, "which", return_value="nvidia-smi")
    def test_driver_query_does_not_filter_devices(self, which, glob):
        result = subprocess.CompletedProcess([], 0, stdout=CSV, stderr="")
        with patch.object(monitor.subprocess, "run", return_value=result) as run:
            data = monitor.collect_gpus()
        self.assertEqual(len(data["gpus"]), 2)
        self.assertEqual(data["errors"], [])
        command = run.call_args.args[0]
        self.assertFalse(any("--id" in arg for arg in command))
        self.assertEqual(run.call_args.kwargs["timeout"], 4)
        json.dumps(data, allow_nan=False)

    @patch.object(monitor.Path, "glob", return_value=[])
    def test_missing_driver_timeout_and_invalid_output(self, glob):
        with patch.object(monitor.shutil, "which", return_value=None):
            data = monitor.collect_gpus()
            self.assertEqual(data["gpus"], [])
            self.assertTrue(data["errors"])
        with patch.object(monitor.shutil, "which", return_value="nvidia-smi"):
            for result in (subprocess.TimeoutExpired("nvidia-smi", 4),
                           subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
                           subprocess.CompletedProcess([], 0, stdout="invalid", stderr="")):
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(monitor.subprocess, "run", **kwargs):
                    data = monitor.collect_gpus()
                self.assertEqual(data["gpus"], [])
                self.assertTrue(data["errors"])

    def test_linux_drm_counters(self):
        with tempfile.TemporaryDirectory() as temp:
            card = Path(temp) / "card0"
            device = card / "device"
            device.mkdir(parents=True)
            for name, value in {"vendor": "0x1002", "product_name": "AMD Test", "mem_info_vram_total": "8589934592",
                                "mem_info_vram_used": "1073741824"}.items():
                (device / name).write_text(value)
            with patch.object(monitor.shutil, "which", return_value=None), patch.object(monitor.Path, "glob", return_value=[card]):
                data = monitor.collect_gpus()
            self.assertEqual(data["gpus"][0]["name"], "AMD Test")
            self.assertEqual(data["gpus"][0]["used_bytes"], 1024 ** 3)
            self.assertEqual(data["errors"], [])

    def test_cache_shared_between_clients_and_expiry(self):
        instance = monitor.GPUMonitor()
        sample = {"gpus": [], "errors": [], "timestamp": 1}
        with patch.object(monitor, "collect_gpus", return_value=sample) as collect:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: instance.snapshot(), range(8)))
            self.assertEqual(collect.call_count, 1)
            self.assertTrue(all(result == sample for result in results))
            instance._expires = 0
            instance.snapshot()
            self.assertEqual(collect.call_count, 2)


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_route_response(self):
        routes = web.RouteTableDef()
        server = types.ModuleType("server")
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=routes))
        spec = importlib.util.spec_from_file_location("gpu_monitor_route_test", ROOT / "__init__.py",
                                                    submodule_search_locations=[str(ROOT)])
        package = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"server": server, spec.name: package}):
            spec.loader.exec_module(package)
        sample = {"gpus": monitor.parse_nvidia_csv(CSV), "errors": [], "timestamp": 1}
        app = web.Application()
        app.add_routes(routes)
        async with TestClient(TestServer(app)) as client:
            with patch.object(package.monitor, "snapshot", return_value=sample):
                response = await client.get("/gpu-monitor/status")
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(await response.json(), sample)
        self.assertEqual(package.WEB_DIRECTORY, "./web")


if __name__ == "__main__":
    unittest.main()
