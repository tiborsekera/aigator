"""Run the shell installer in isolated targets with a synthetic, offline git clone."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Bash installer')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "custom app ' $literal"
        self.bin = self.root / 'bin'
        fakebin = self.root / 'fakebin'
        fakebin.mkdir()
        git = fakebin / 'git'
        git.write_text(f'''#!{sys.executable}
import os, shutil, sys
from pathlib import Path
if os.environ.get('AIGATOR_TEST_FAIL'):
    sys.exit(42)
target = Path(sys.argv[-1])
shutil.copytree(Path(os.environ['AIGATOR_TEST_SOURCE']) / 'aigator', target / 'aigator')
''')
        git.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ['PATH'],
            'AIGATOR_TEST_SOURCE': str(ROOT), 'AIGATOR_INSTALL_DIR': str(self.target), 'AIGATOR_BIN_DIR': str(self.bin)}

    def install(self):
        return subprocess.run(['bash', str(ROOT / 'install.sh')], cwd=self.root, env=self.env, capture_output=True, text=True)

    def test_unknown_target_preserved(self):
        self.target.mkdir()
        precious = self.target / 'unrelated.txt'
        precious.write_text('keep me')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing unmarked', result.stderr)
        self.assertEqual(precious.read_text(), 'keep me')

    def test_custom_wrapper_reinstall_and_failure(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        env = {**os.environ}
        env.pop('AIGATOR_INSTALL_DIR', None)
        result = subprocess.run([str(self.bin / 'aigator'), '--help'], cwd=self.root, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        precious = self.target / 'local-edit.txt'
        precious.write_text('preserved backup')
        self.env['AIGATOR_TEST_FAIL'] = '1'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(precious.read_text(), 'preserved backup')
        self.env.pop('AIGATOR_TEST_FAIL')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(self.root.glob('.aigator-backup.*/app/local-edit.txt'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), 'preserved backup')
