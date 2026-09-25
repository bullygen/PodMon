import unittest
from datetime import datetime, timezone

import httpx

from app.models import parse_stats, parse_top
from app.podman_client import PodmanClient, PodmanError, decode_logs
from app.process_status import assess, candidate


class ParserTests(unittest.TestCase):
    def test_process_discovery_without_names(self):
        processes = parse_top({'Titles': ['PID', '%CPU', '%MEM', 'ETIME', 'ARGS'], 'Processes': [
            ['1', '0', '0', '10:00', 'sleep infinity'],
            ['12', '1200', '15', '01:20', 'python -u smbh_cv_run.py all'],
            ['21', '23', '1', '03:00', '/workspace/custom_solver'],
        ]})
        self.assertEqual(processes[0]['cpu_percent'], 1200)
        self.assertTrue(candidate(processes[0]))
        self.assertTrue(candidate(processes[1]))
        self.assertFalse(candidate(processes[2]))
        self.assertTrue(candidate({'command': '/usr/bin/env python train.py', 'cpu_percent': 0}))
        result = assess(processes, 'running', datetime.now(timezone.utc).isoformat())
        self.assertEqual(result['status'], 'candidate')
        self.assertEqual(result['activity'], 'active')
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(assess([], 'running', None)['status'], 'no_candidate')
        self.assertEqual(assess([{'command': 'python idle.py', 'cpu_percent': 0}], 'running', None)['activity'], 'unknown')
        self.assertEqual(assess([], 'exited', None)['status'], 'stopped')

    def test_stats_and_log_frames(self):
        stats = parse_stats([{'ContainerID': 'abc', 'CPU': 1250, 'MemUsage': 1024, 'PIDs': 0}], 'abcdef')
        self.assertEqual(stats['equivalent_cpus'], 12.5)
        self.assertEqual(stats['pids'], 0)
        payload = b'2026-09-25T10:00:00Z hello\n'
        frame = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, 'big') + payload
        decoded = decode_logs(frame)
        self.assertIn('hello', decoded['text'])
        self.assertIsNotNone(decoded['last_log_at'])


class PodmanClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_uses_only_named_get_routes(self):
        requests = []
        def responder(request):
            requests.append((request.method, request.url.path))
            path = request.url.path
            if path.endswith('/containers/json'):
                return httpx.Response(200, json=[{'Id': 'abc', 'Names': ['test']}])
            if path.endswith('/stats'):
                return httpx.Response(200, json=[{'ContainerID': 'abc', 'CPU': 23}])
            if path.endswith('/top'):
                return httpx.Response(200, json={'Titles': [], 'Processes': []})
            if path.endswith('/logs'):
                return httpx.Response(200, content=b'2026-09-25T10:00:00Z line\n')
            return httpx.Response(200, json={'State': {'Status': 'running'}})
        client = PodmanClient('/unused')
        await client.close()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url='http://podman')
        self.assertEqual((await client.containers())[0]['Id'], 'abc')
        await client.inspect('abc')
        await client.top('abc')
        await client.stats()
        self.assertIn('line', (await client.logs('abc'))['text'])
        self.assertTrue(all(method == 'GET' for method, _ in requests))
        self.assertEqual(len(requests), 5)
        await client.close()

    async def test_top_fallback(self):
        calls = []
        def responder(request):
            calls.append(str(request.url))
            if len(calls) == 1:
                return httpx.Response(400, json={'error': 'unsupported descriptor'})
            return httpx.Response(200, json={'Titles': ['PID', 'ARGS'], 'Processes': [['1', 'python job.py']]})
        client = PodmanClient('/unused')
        await client.close()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url='http://podman')
        self.assertEqual((await client.top('abc'))['Processes'][0][1], 'python job.py')
        self.assertEqual(len(calls), 2)
        await client.close()


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_every_container_and_survives_broken_one(self):
        from app import main
        main.LOG_CACHE.clear()
        class FakeClient:
            async def containers(self):
                return [{'Id': 'abc123', 'Names': ['solver'], 'State': 'running'}, {'Id': 'bad456', 'Names': ['old'], 'State': 'exited'}]
            async def stats(self):
                return [{'ContainerID': 'abc123', 'CPU': 1250, 'MemUsage': 1024, 'PIDs': 2}]
            async def inspect(self, cid):
                if cid == 'bad456':
                    raise PodmanError('gone')
                return {'State': {'Status': 'running', 'StartedAt': '2026-09-25T00:00:00Z'}, 'HostConfig': {'Memory': 2048}}
            async def top(self, cid):
                return {'Titles': ['PID', 'PCPU', 'PMEM', 'ETIME', 'ARGS'], 'Processes': [['12', '50', '1', '02:00', 'python work.py']]}
            async def logs(self, cid):
                if cid == 'bad456':
                    raise PodmanError('log unavailable')
                return {'text': '2026-09-25T10:00:00Z work', 'last_log_at': '2026-09-25T10:00:00+00:00'}
        await main.collect(FakeClient(), refresh_logs=True)
        self.assertEqual([c['name'] for c in main.SNAPSHOT['containers']], ['solver', 'old'])
        self.assertEqual(main.SNAPSHOT['containers'][0]['cpu_percent'], 1250)
        self.assertEqual(main.SNAPSHOT['containers'][0]['memory_limit'], 2048)
        self.assertEqual(main.SNAPSHOT['containers'][1]['status'], 'unknown')

    async def test_api_and_discovered_log_whitelist(self):
        from app import main
        main.SNAPSHOT = {'updated_at': 'now', 'socket': 'reachable', 'error': None, 'host': {'hostname': 'test'}, 'containers': [{'id': 'abc', 'name': 'solver', 'processes': [{'pid': '1'}]}]}
        main.LOG_CACHE.clear()
        main.LOG_CACHE['abc'] = {'text': 'hello', 'last_log_at': None}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            self.assertEqual((await client.get('/api/health')).json(), {'status': 'ok'})
            self.assertEqual((await client.get('/api/containers')).json()['containers'][0]['name'], 'solver')
            self.assertEqual((await client.get('/api/containers/solver/processes')).json()[0]['pid'], '1')
            self.assertEqual((await client.get('/api/containers/solver/log')).text, 'hello')
            self.assertEqual((await client.get('/api/containers/other/log')).status_code, 404)
            self.assertEqual((await client.post('/api/containers/solver/stop')).status_code, 404)
