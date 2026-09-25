"""Only named, read-only Podman REST calls over a local Unix socket."""
import re
from datetime import datetime, timezone

import httpx


class PodmanError(Exception):
    pass


def decode_logs(payload):
    """Handle both plain Podman logs and Docker-compatible multiplexed frames."""
    if len(payload) >= 8 and payload[0] in (1, 2) and payload[1:4] == b'\0\0\0':
        pos, frames = 0, []
        while pos + 8 <= len(payload):
            length = int.from_bytes(payload[pos + 4:pos + 8], 'big')
            if payload[pos] not in (1, 2) or payload[pos + 1:pos + 4] != b'\0\0\0' or pos + 8 + length > len(payload):
                break
            frames.append(payload[pos + 8:pos + 8 + length])
            pos += 8 + length
        payload = b''.join(frames) if frames else payload
    text = payload.decode('utf-8', errors='replace')
    lines = text.splitlines()[-50:]
    last_at = None
    for line in reversed(lines):
        match = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))\s', line)
        if match:
            try:
                last_at = datetime.fromisoformat(match.group(1).replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
                break
            except ValueError:
                pass
    return {'text': '\n'.join(lines), 'last_log_at': last_at}


class PodmanClient:
    def __init__(self, socket_path='/run/podman/podman.sock'):
        self.client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket_path), base_url='http://podman', timeout=httpx.Timeout(5.0))

    async def close(self):
        await self.client.aclose()

    async def get(self, path, params=None):
        try:
            response = await self.client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PodmanError(str(exc)) from exc

    async def containers(self):
        return await self.get('/v4.0.0/libpod/containers/json', {'all': 'true'})

    async def inspect(self, container_id):
        return await self.get(f'/v4.0.0/libpod/containers/{container_id}/json')

    async def top(self, container_id):
        path = f'/v4.0.0/containers/{container_id}/top'
        try:
            return await self.get(path, {'ps_args': 'pid,ppid,pcpu,pmem,etime,args'})
        except PodmanError:
            return await self.get(path, {'ps_args': 'pid,ppid,args'})

    async def stats(self):
        return await self.get('/v4.0.0/libpod/containers/stats', {'stream': 'false'})

    async def logs(self, container_id):
        path = f'/v4.0.0/libpod/containers/{container_id}/logs'
        params = {'stdout': 'true', 'stderr': 'true', 'tail': '50', 'timestamps': 'true', 'follow': 'false'}
        try:
            async with self.client.stream('GET', path, params=params) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    remaining = 262_144 - size
                    if remaining <= 0:
                        break
                    chunks.append(chunk[:remaining])
                    size += min(len(chunk), remaining)
            return decode_logs(b''.join(chunks))
        except httpx.HTTPError as exc:
            raise PodmanError(str(exc)) from exc
