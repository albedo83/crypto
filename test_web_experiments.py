"""Read-only experiment route, role isolation, and browser rendering contract."""
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from alfred.web.app import create_app


class ExperimentWebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {
            'DASHBOARD_USER': 'admin', 'DASHBOARD_PASS': 'test-only-password',
            'AUTH_SALT': 'test-only-salt', 'ALFRED_ROOT_PATH': '',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager = SimpleNamespace(snapshot=lambda: {'enabled': True, 'books': [{'equity': None}]})
        self.master = SimpleNamespace(data_dir=self.directory.name, experiments=self.manager)
        self.client = TestClient(create_app({}, self.master))
        self.addCleanup(self.client.close)

    def cookie(self, role):
        message = f'{int(time.time())}:{role}'
        secret = hashlib.sha256(b'test-only-passwordtest-only-salt').digest()
        return message + ':' + hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()[:16]

    def request(self, role=None, method='GET'):
        self.client.cookies.clear()
        if role:
            self.client.cookies.set('alfred_session', self.cookie(role))
        return self.client.request(method, '/api/master/experiments')

    def test_external_context_admin_only_and_no_cache(self):
        with patch('ai_external_context.read_cache', return_value={'status':'ok','facts':[]}):
            self.client.cookies.set('alfred_session', self.cookie('admin'))
            r=self.client.get('/api/master/external-context')
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.headers['cache-control'],'no-store')
            self.assertEqual(r.json()['facts'],[])
            self.client.cookies.set('alfred_session', self.cookie('bot:live'))
            self.assertEqual(self.client.get('/api/master/external-context').status_code,403)
            self.client.cookies.clear()
            self.assertEqual(self.client.get('/api/master/external-context').status_code,401)

    def test_admin_snapshot_preserves_unknown_values(self):
        response = self.request('admin')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['books'][0]['equity'])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_bot_roles_forbidden_without_calling_manager(self):
        def forbidden_snapshot():
            self.fail('Unauthorized snapshot access')
        self.manager.snapshot = forbidden_snapshot
        for role in ('bot:live', 'bot:paper', 'bot:junior', 'bot:baby'):
            self.assertEqual(self.request(role).status_code, 403)

    def test_anonymous_unauthorized(self):
        self.assertEqual(self.request().status_code, 401)

    def test_route_is_read_only(self):
        self.assertEqual(self.request('admin', 'POST').status_code, 405)

    def test_missing_manager_disabled(self):
        del self.master.experiments
        self.assertEqual(self.request('admin').json(), {'enabled': False, 'books': [], 'references': []})

    def test_failed_snapshot_503_without_internal_detail(self):
        def broken():
            raise RuntimeError('private diagnostic')
        self.manager.snapshot = broken
        with self.assertLogs('alfred', level='ERROR'):
            response = self.request('admin')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private diagnostic', response.text)
        self.assertNotIn('books', response.json())


if __name__ == '__main__':
    unittest.main()
