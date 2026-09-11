"""Exercise the Windows quick installer offline, without changing User PATH."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows PowerShell installer')
class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'custom app'
        self.bin = self.root / 'bin'
        self.env = {**os.environ, 'AIGATOR_INSTALL_DIR': str(self.target),
                    'AIGATOR_BIN_DIR': str(self.bin), 'AIGATOR_NO_MODIFY_PATH': '1',
                    'AIGATOR_TEST_SOURCE': str(ROOT)}
        self.script = self.root / 'run.ps1'
        self.script.write_text('''$ErrorActionPreference = 'Stop'
function git {
    if ($env:AIGATOR_TEST_FAIL) { throw 'Synthetic download failure' }
    $target = $args[-1]
    New-Item -ItemType Directory -Path $target | Out-Null
    Copy-Item -LiteralPath "$env:AIGATOR_TEST_SOURCE/aigator" -Destination $target -Recurse
    $global:LASTEXITCODE = 0
}
& "$env:AIGATOR_TEST_SOURCE/install.ps1"
''', encoding='utf-8')

    def install(self):
        return subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive',
                               '-ExecutionPolicy', 'Bypass', '-File', str(self.script)],
                              cwd=self.root, env=self.env, capture_output=True, text=True)

    def test_install_upgrade_and_download_failure(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run(['cmd.exe', '/d', '/c', str(self.bin / 'aigator.cmd'), '--help'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('multi-platform', result.stdout)
        precious = self.target / 'local-edit.txt'
        precious.write_text('keep')
        self.env['AIGATOR_TEST_FAIL'] = '1'
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(precious.read_text(), 'keep')
        self.env.pop('AIGATOR_TEST_FAIL')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list(self.root.glob('custom app.backup.*/local-edit.txt'))), 1)

    def test_unknown_target_and_launcher_preserved(self):
        self.target.mkdir()
        precious = self.target / 'unrelated.txt'
        precious.write_text('keep')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(precious.read_text(), 'keep')
        shutil.rmtree(self.target)
        self.bin.mkdir()
        launcher = self.bin / 'aigator.cmd'
        launcher.write_text('precious launcher')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(launcher.read_text(), 'precious launcher')

    def test_locked_launcher_rolls_back_app(self):
        self.assertEqual(self.install().returncode, 0)
        precious = self.target / 'local-edit.txt'
        precious.write_text('keep')
        previous = (self.bin / 'aigator.cmd').read_bytes()
        self.script.write_text(self.script.read_text().replace(
            '& "$env:AIGATOR_TEST_SOURCE/install.ps1"', '''
$lock = [IO.File]::Open("$env:AIGATOR_BIN_DIR/aigator.cmd", 'Open', 'Read', 'Read')
try { & "$env:AIGATOR_TEST_SOURCE/install.ps1" } finally { $lock.Dispose() }
'''), encoding='utf-8')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(precious.read_text(), 'keep')
        self.assertTrue((self.target / '.aigator-install').exists())
        self.assertEqual((self.bin / 'aigator.cmd').read_bytes(), previous)
