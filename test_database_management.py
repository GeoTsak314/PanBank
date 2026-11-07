"""Database management safeguards for PanBank Web v4.0.1."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import core
import panbank


class TestDatabaseManagement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.allowed = self.base / 'approved'
        self.blocked = self.base / 'private'
        self.allowed.mkdir()
        self.blocked.mkdir()
        self.db = self.allowed / 'finance.sqlite3'
        self.store = core.Store(self.db)
        self.store.close()
        self.env = patch.dict(os.environ, {'PANBANK_DB_ALLOWED_DIRS': str(self.allowed),
                                         'PANBANK_PASSWORD':'testing-password'}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_valid_database(self):
        self.assertEqual(panbank.validate_db_candidate(str(self.db)), self.db)

    def test_different_folder_blocked(self):
        other = self.blocked / 'other.sqlite3'
        s = core.Store(other); s.close()
        with self.assertRaisesRegex(ValueError, 'allowed server directory'):
            panbank.validate_db_candidate(str(other))

    def test_not_sqlite(self):
        fake = self.allowed / 'fake.sqlite3'
        fake.write_text('not a sqlite database')
        with self.assertRaisesRegex(ValueError, 'SQLite header'):
            panbank.validate_db_candidate(str(fake))

    def test_not_panbank(self):
        import sqlite3
        other = self.allowed / 'other.sqlite3'
        with sqlite3.connect(other) as db:
            db.execute('CREATE TABLE example(x INTEGER)')
        with self.assertRaisesRegex(ValueError, 'missing tables'):
            panbank.validate_db_candidate(str(other))

    def test_missing_selected_db_does_not_create_empty_file(self):
        config = self.base / 'panbank_db_config.json'
        missing = self.allowed / 'deleted.sqlite3'
        config.write_text(json.dumps({'database_path': str(missing)}))
        with patch.object(core, 'CONFIG_FILE', config):
            with self.assertRaisesRegex(RuntimeError, 'database missing'):
                core.configured_db_path()
        self.assertFalse(missing.exists())

    def test_authenticated_csrf_flow(self):
        import re
        config = self.base / 'setting.json'
        with patch.object(panbank, 'SETTINGS_FILE', config):
            with TestClient(panbank.app, follow_redirects=False) as client:
                self.assertEqual(client.get('/settings/database').status_code, 401)
                self.assertEqual(client.post('/login', data={'password':panbank.PASSWORD}).status_code,303)
                page = client.get('/settings/database')
                self.assertEqual(page.status_code, 200)
                token = re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
                rejected = client.post('/settings/database', data={
                    'csrf': token,'database_path':str(self.db),'confirmation':'WRONG'})
                self.assertEqual(rejected.status_code, 400)
                self.assertFalse(config.exists())
                saved = client.post('/settings/database',data={
                    'csrf': token,'database_path':str(self.db),'confirmation':'SWITCH'})
                self.assertEqual(saved.status_code, 303)
                self.assertEqual(json.loads(config.read_text())['database_path'],str(self.db))


if __name__ == '__main__':
    unittest.main()
