import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.host_metrics import HostMetrics
from app.models import parse_stats, parse_top
from app.podman_client import PodmanClient, PodmanError
from app.process_status import assess


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'level': record.levelname, 'message': record.getMessage()}, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger(__name__)
HOST = HostMetrics()
SNAPSHOT = {'updated_at': None, 'socket': 'unknown', 'error': None, 'host': HOST.collect(), 'containers': []}
LOG_CACHE = {}
POLL_SECONDS = max(3, min(60, int(os.getenv('PODMON_POLL_SECONDS', '5'))))


def timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return None


def get_name(raw):
    names = raw.get('Names') or raw.get('names') or []
    if isinstance(names, list) and names:
        return str(names[0]).lstrip('/')
    return str(raw.get('Name') or raw.get('name') or raw.get('Id') or raw.get('ID') or 'unknown')


def state_of(raw, detail):
    state = detail.get('State') or {}
    value = (state.get('Status') if isinstance(state, dict) else None) or raw.get('State') or raw.get('Status') or 'unknown'
    return str(value).lower() if isinstance(value, str) else 'unknown'


async def collect_one(client, raw, stats_data, refresh_logs):
    cid = str(raw.get('Id') or raw.get('ID') or raw.get('id') or '')
    name = get_name(raw)
    card = {'id': cid, 'name': name, 'status': 'unknown', 'started_at': None, 'uptime_seconds': None, 'exit_code': None, 'cpu_percent': None, 'equivalent_cpus': None, 'memory_used': None, 'memory_limit': None, 'memory_percent': None, 'pids': None, 'processes': [], 'process_assessment': {'status': 'unknown', 'activity': 'unknown', 'candidates': [], 'last_activity_seconds': None}, 'has_log': cid in LOG_CACHE, 'last_log_at': None, 'error': None}
    detail = {}
    top_available = False
    try:
        detail = await client.inspect(cid)
        if not isinstance(detail, dict):
            raise ValueError('inspect response is not an object')
        card['status'] = state_of(raw, detail)
    except Exception as exc:
        log.warning('inspect_failed container=%s error=%s', name, exc)
        card['error'] = 'container details unavailable'
    state = detail.get('State') or {}
    if isinstance(state, dict):
        card['started_at'] = state.get('StartedAt')
        if card['status'] in ('exited', 'dead'):
            card['exit_code'] = state.get('ExitCode')
    started = timestamp(card['started_at'])
    if started:
        end = datetime.now(timezone.utc).timestamp() if card['status'] == 'running' else timestamp(state.get('FinishedAt')) if isinstance(state, dict) else None
        if end and end >= started:
            card['uptime_seconds'] = int(end - started)
    if card['status'] == 'running':
        try:
            card['processes'] = parse_top(await client.top(cid))
            top_available = True
        except Exception as exc:
            log.warning('top_failed container=%s error=%s', name, exc)
            card['error'] = 'process list unavailable'
        stats = parse_stats(stats_data, cid)
        for key in ('cpu_percent', 'equivalent_cpus', 'memory_used', 'pids'):
            card[key] = stats[key]
        try:
            limit = int((detail.get('HostConfig') or {}).get('Memory') or 0)
            card['memory_limit'] = limit if limit > 0 else None
        except (TypeError, ValueError, AttributeError):
            pass
        if card['memory_limit'] and card['memory_used'] is not None:
            card['memory_percent'] = round(100 * card['memory_used'] / card['memory_limit'], 1)
    if refresh_logs or cid not in LOG_CACHE:
        try:
            LOG_CACHE[cid] = await client.logs(cid)
        except Exception as exc:
            log.warning('logs_failed container=%s error=%s', name, exc)
            LOG_CACHE.setdefault(cid, {'text': '', 'last_log_at': None})
    cached_log = LOG_CACHE.get(cid, {})
    card['has_log'] = bool(cached_log.get('text'))
    card['last_log_at'] = cached_log.get('last_log_at')
    card['process_assessment'] = assess(card['processes'], card['status'], card['last_log_at'], top_available)
    return card


async def collect(client, refresh_logs=False):
    global SNAPSHOT
    host = HOST.collect()
    try:
        raw = await client.containers()
        if not isinstance(raw, list):
            raise PodmanError('container list is not an array')
        try:
            stats_data = await client.stats()
        except Exception as exc:
            log.warning('stats_failed error=%s', exc)
            stats_data = []
        entries = [item for item in raw if isinstance(item, dict)]
        semaphore = asyncio.Semaphore(8)
        async def bounded(item):
            async with semaphore:
                return await collect_one(client, item, stats_data, refresh_logs)
        results = await asyncio.gather(*(bounded(item) for item in entries), return_exceptions=True)
        cards = []
        for item, result in zip(entries, results):
            if isinstance(result, Exception):
                log.error('container_collection_failed container=%s error=%s', get_name(item), result)
                cards.append({'id': str(item.get('Id') or ''), 'name': get_name(item), 'status': 'unknown', 'processes': [], 'process_assessment': {'status': 'unknown', 'activity': 'unknown'}, 'has_log': False, 'error': 'container details unavailable'})
            else:
                cards.append(result)
        valid_ids = {card['id'] for card in cards}
        for cid in tuple(LOG_CACHE):
            if cid not in valid_ids:
                del LOG_CACHE[cid]
        SNAPSHOT = {'updated_at': datetime.now(timezone.utc).isoformat(), 'socket': 'reachable', 'error': None, 'host': host, 'containers': cards}
    except Exception as exc:
        log.warning('collector_failed error=%s', exc)
        SNAPSHOT = {**SNAPSHOT, 'socket': 'unavailable', 'error': str(exc), 'host': host}


async def collector_loop(client):
    cycle = 0
    while True:
        await collect(client, refresh_logs=(cycle % 3 == 0))
        cycle += 1
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def lifespan(app):
    client = PodmanClient(os.getenv('PODMAN_SOCKET', '/run/podman/podman.sock'))
    task = asyncio.create_task(collector_loop(client))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await client.close()


app = FastAPI(title='podmon', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
ROOT = Path(__file__).resolve().parent.parent
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
def index():
    return FileResponse(ROOT / 'app' / 'templates' / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.get('/api/host')
def host():
    return SNAPSHOT['host']


@app.get('/api/containers')
def containers():
    return {key: SNAPSHOT[key] for key in ('updated_at', 'socket', 'error', 'containers')}


def card_by_name(name):
    for card in SNAPSHOT['containers']:
        if card['name'] == name:
            return card
    raise HTTPException(404, 'container not found')


@app.get('/api/containers/{name}/processes')
def processes(name: str):
    return card_by_name(name)['processes']


@app.get('/api/containers/{name}/log')
def container_log(name: str):
    card = card_by_name(name)
    cached = LOG_CACHE.get(card['id'])
    if not cached:
        raise HTTPException(404, 'log unavailable')
    return PlainTextResponse(cached['text'], headers={'Cache-Control': 'no-store'})


@app.get('/api/containers/{name}')
def container(name: str):
    return card_by_name(name)
