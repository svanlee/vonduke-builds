"""
Sidecar server stub — the asyncio WebSocket server a sidecar machine (e.g.
robocar-hub, Pi 4 / RDK X5, running envs/robocar_env.py) starts to receive
high-level directives from the AKSUMAEL overseer running elsewhere on the
network. See sidecar/__init__.py for the daemon/sidecar topology and
sidecar/protocol.py for the message schema.

This is a scaffold: importable and runnable standalone, but nothing in
core/runtime.py dials out to it yet. Requires the `websockets` package
(pip install websockets) to actually serve — importing this module without
it installed still works, it just can't start.
"""
import asyncio

from sidecar import protocol
from sidecar.auth import validate_token

try:
    import websockets
    _HAVE_WEBSOCKETS = True
except ImportError:
    _HAVE_WEBSOCKETS = False

DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 8765


class SidecarServer:
    """Accepts directive connections from the AKSUMAEL daemon. Subclass or
    monkey-patch `on_directive` to actually act on incoming directives
    (e.g. forward into envs/robocar_env.py's control surface)."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port

    async def on_directive(self, payload: dict) -> dict:
        """Override in a subclass. Default: acknowledge and no-op. Return
        value becomes the ACK/ERROR payload sent back to the daemon."""
        print(f'[SIDECAR] directive received (no handler wired): {payload}')
        return {'accepted': True}

    async def _handle(self, websocket):
        async for raw in websocket:
            msg = protocol.from_json(raw)
            if not protocol.validate_message(msg):
                await websocket.send(protocol.to_json(
                    protocol.build_message(protocol.ERROR, {'reason': 'malformed message'})))
                continue

            claims = validate_token(msg.get('auth_token'))
            if claims is None:
                await websocket.send(protocol.to_json(
                    protocol.build_message(protocol.ERROR, {'reason': 'invalid or expired token'})))
                continue

            if msg['type'] == protocol.HEARTBEAT:
                await websocket.send(protocol.to_json(protocol.build_message(protocol.ACK)))
            elif msg['type'] == protocol.DIRECTIVE:
                try:
                    result = await self.on_directive(msg['payload'])
                    await websocket.send(protocol.to_json(
                        protocol.build_message(protocol.ACK, result)))
                except Exception as e:
                    await websocket.send(protocol.to_json(
                        protocol.build_message(protocol.ERROR, {'reason': str(e)})))

    async def serve_forever(self):
        if not _HAVE_WEBSOCKETS:
            raise RuntimeError(
                "the 'websockets' package is required to run sidecar.server "
                "(pip install websockets) — sidecar/auth.py and "
                "sidecar/protocol.py work without it")
        async with websockets.serve(self._handle, self.host, self.port):
            print(f'[SIDECAR] listening on {self.host}:{self.port}')
            await asyncio.Future()  # run until cancelled


if __name__ == '__main__':
    asyncio.run(SidecarServer().serve_forever())
