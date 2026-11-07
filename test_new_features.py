"""Regression tests for PanBank Web v4.0.3."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core
import panbank
from fastapi.testclient import TestClient


class AnnualExpenseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.Store(Path(self.tmp.name) / 'trial.sqlite3')
        core.migrate_web(self.db)
        self.db.run("INSERT INTO categories(kind,name) VALUES ('expense','Education')")
        self.db.run("INSERT INTO categories(kind,name,parent_id,expense_allocation) VALUES ('expense','Annual tuition',1,'annual_12')")
        self.db.run("INSERT INTO categories(kind,name,parent_id) VALUES ('expense','Meals',1)")
        self.db.run("INSERT INTO categories(kind,name) VALUES ('income','Salary')")
        self.db.run("INSERT INTO accounts(name) VALUES ('Test account')")
        self.db.run("INSERT INTO expenses(tx_date,amount,category_id) VALUES ('2026-04-15',120000,2)")
        self.db.run("INSERT INTO expenses(tx_date,amount,category_id) VALUES ('2026-04-16',20000,3)")
        for dt in ('2026-01-01','2026-05-01','2027-01-01'):
            self.db.run('INSERT INTO snapshots(snapshot_date,account_id,amount) VALUES (?,?,?)', (dt,1,1000000))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_monthly_allocation_preserves_total(self):
        months = [core.get_monthly(self.db,2026,month) for month in range(1,13)]
        self.assertEqual([m['allocated_expenses'] for m in months],[10000]*12)
        self.assertEqual(months[3]['actual_cash_expenses'],140000)
        self.assertEqual(months[3]['expenses'],30000)
        self.assertEqual(months[0]['expenses'],10000)
        self.assertEqual(sum(m['expenses'] for m in months),140000)

    def test_partial_year_reconciliation_uses_allocated_months(self):
        r=core.year_reconciliation(self.db,2026)
        self.assertEqual(r['expenses'],140000)
        self.db.run("DELETE FROM snapshots WHERE snapshot_date='2027-01-01'")
        r=core.year_reconciliation(self.db,2026)
        self.assertEqual(r['end'],'2026-05-01')
        self.assertEqual(r['expenses'],40000+20000) # Jan-Apr allocation plus normal April expense

    def test_annual_parent_prohibited(self):
        with self.assertRaisesRegex(Exception,'Only expense subcategories'):
            self.db.run("UPDATE categories SET expense_allocation='annual_12' WHERE id=1")
        with self.assertRaisesRegex(Exception,'Only expense subcategories'):
            self.db.run("UPDATE categories SET kind='income' WHERE id=2")

    def test_edit_form_exposes_setting(self):
        with TestClient(panbank.app,follow_redirects=False) as client:
            self.assertEqual(client.post('/login',data={'password':panbank.PASSWORD}).status_code,303)
            # Inspect a temporary live DB without replacing the global default:
            with patch.object(panbank,'DB_PATH',self.db.path):
                html=client.get('/edit/Categories/2')
                self.assertEqual(html.status_code,200)
                self.assertIn('Expense allocation (subcategories only)',html.text)


class LocalSettingsTests(unittest.TestCase):
    def test_password_hash_not_plain_text(self):
        password='Example-Password-4321!'
        encoded=panbank.hash_password(password)
        self.assertNotIn(password,encoded)
        self.assertTrue(encoded.startswith('pbkdf2_sha256$'))
        with patch.object(panbank,'PASSWORD',''),patch.object(panbank,'PASSWORD_HASH',encoded):
            self.assertTrue(panbank.verify_password(password))
            self.assertFalse(panbank.verify_password('incorrect password'))

    def test_any_password_length_including_empty(self):
        for password in ('', '1', 'abc', 'Πα', 'Example-Password-4321!'):
            with self.subTest(password=password):
                encoded=panbank.hash_password(password,iterations=100000)
                self.assertTrue(encoded.startswith('pbkdf2_sha256$'))
                with patch.object(panbank,'PASSWORD',''),patch.object(panbank,'PASSWORD_HASH',encoded):
                    self.assertTrue(panbank.verify_password(password))
                    self.assertFalse(panbank.verify_password(password+'wrong'))

    def test_blank_login_form_is_submittable(self):
        html=(Path(__file__).parent/'templates'/'login.html').read_text(encoding='utf-8')
        self.assertNotIn('required autofocus',html)
        with TestClient(panbank.app,follow_redirects=False) as client:
            with patch.object(panbank,'PASSWORD',''),patch.object(panbank,'PASSWORD_HASH',panbank.hash_password('',iterations=100000)):
                self.assertEqual(client.post('/login',data={'password':''}).status_code,303)

    def test_saved_home_directory_and_path(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)
            another=home/'db';another.mkdir()
            cfg=home/'panbank_settings.json'
            with patch.object(panbank,'SETTINGS_FILE',cfg):
                panbank.save_app_settings({'database_directory':str(another),'password_hash':'a-salted-hash',
                     'session_secret':'z'*48})
            content=cfg.read_text()
            self.assertIn(str(another),content)
            self.assertNotIn('Example-Password-4321!',content)
            with patch.object(core,'SETTINGS_FILE',cfg), patch.object(core,'CONFIG_FILE',home/'nonexistent.json'):
                with patch.dict(os.environ,{},clear=True):
                    self.assertEqual(core.configured_db_path(),another/'panbank.sqlite3')
            if os.name!='nt':self.assertEqual(cfg.stat().st_mode & 0o077,0)

if __name__=='__main__':unittest.main()
