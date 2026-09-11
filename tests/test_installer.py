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

    def test_unrelated_launcher_and_symlink_preserved(self):
        self.bin.mkdir()
        launcher = self.bin / 'aigator'
        launcher.write_text('precious executable')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(launcher.read_text(), 'precious executable')
        launcher.unlink()
        destination = self.root / 'precious'
        destination.write_text('keep')
        launcher.symlink_to(destination)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(destination.read_text(), 'keep')
        self.assertFalse(self.target.exists())

    def test_launcher_failure_restores_app(self):
        self.assertEqual(self.install().returncode, 0)
        precious = self.target / 'local-edit.txt'
        precious.write_text('keep')
        launcher = self.bin / 'aigator'
        previous = launcher.read_bytes()
        fake_mv = self.root / 'fakebin' / 'mv'
        fake_mv.write_text(f"""#!{sys.executable}
import os, sys
if sys.argv[-1] == os.environ['AIGATOR_BIN_DIR'] + '/aigator':
    sys.exit(42)
os.execv({shutil.which('mv')!r}, ['mv'] + sys.argv[1:])
""")
        fake_mv.chmod(0o755)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(precious.read_text(), 'keep')
        self.assertEqual(launcher.read_bytes(), previous)

    def test_launcher_isolates_imports_and_custom_path(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.bin), result.stdout)
        hostile = self.root / 'aigator'
        hostile.mkdir()
        (hostile / '__init__.py').write_text('raise RuntimeError("wrong package")')
        env = {**self.env, 'PYTHONPATH': str(self.root)}
        result = subprocess.run([str(self.bin / 'aigator'), '--help'], cwd=self.root,
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('multi-platform', result.stdout)

    def test_bin_inside_app_rejected(self):
        self.env['AIGATOR_BIN_DIR'] = str(self.target / 'bin')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse(self.target.exists())

    def test_archive_download_without_git(self):
        import tarfile
        archive = self.root / 'source.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            bundle.add(ROOT / 'aigator', arcname='aigator-main/aigator')
        # An explicit minimal PATH ensures this really exercises the no-Git branch.
        minimal = self.root / 'minimal'
        minimal.mkdir()
        for command in ('bash', 'mkdir', 'mktemp', 'rm', 'tar', 'chmod', 'mv', 'dirname', 'gzip'):
            (minimal / command).symlink_to(shutil.which(command))
        (minimal / 'python3').symlink_to(sys.executable)
        curl = minimal / 'curl'
        curl.write_text(f"""#!{sys.executable}
import shutil, sys
shutil.copyfile({str(archive)!r}, sys.argv[sys.argv.index('-o') + 1])
""")
        curl.chmod(0o755)
        self.env['PATH'] = str(minimal)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / '.aigator-install').exists())
        result = subprocess.run([str(self.bin / 'aigator'), '--help'], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
