import asyncio

from aiohttp import web
from server import PromptServer

from .monitor import GPUMonitor

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

monitor = GPUMonitor()


@PromptServer.instance.routes.get("/gpu-monitor/status")
async def gpu_status(request):
    snapshot = await asyncio.to_thread(monitor.snapshot)
    return web.json_response(snapshot, headers={"Cache-Control": "no-store"})

