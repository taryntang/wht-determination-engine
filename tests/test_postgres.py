"""Opt-in real SQL verification after applying schema in a disposable project."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

class PostgresTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('psql') and os.environ.get('WHT_TEST_DATABASE_URL'),
                         'Set WHT_TEST_DATABASE_URL and install psql for real PostgreSQL tests')
    def test_transaction_history_and_controls(self):
        env={**os.environ,'PGDATABASE':os.environ['WHT_TEST_DATABASE_URL']}
        path=Path(__file__).resolve().parents[1]/'sql/test_controls.sql'
        result=subprocess.run(['psql','-X','-v','ON_ERROR_STOP=1','-f',str(path)],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
