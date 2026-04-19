"""Route handlers for the workspace HTTP API.

Each route module exposes one or more ``async def handle_*`` functions
with the aiohttp signature ``(request) -> web.Response``. ``app.py``
registers them on the aiohttp router.
"""
