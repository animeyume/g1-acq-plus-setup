"""Run the portable entry point against harmless fixtures, never the installer."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_commands import windows_portable_command, windows_short_command

FIXTURE = b"[Console]::WriteLine('G1_TEST_INSTALLER_RAN')\n"
FAILED = b"[Console]::WriteLine('G1_TEST_INSTALLER_RAN')\nexit 7\n"
CMDLET = b"[CmdletBinding()]\nparam([string]$InstallDir)\n[Console]::WriteLine('G1_TEST_INSTALLER_RAN')\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/404':
            self.send_error(404)
            return
        data = FAILED if self.path == '/failed' else FIXTURE
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pwsh = (os.environ.get('G1_TEST_PWSH') or shutil.which('pwsh') or
                    str(ROOT.parent.parent / '.tools/pwsh/pwsh'))
        if not Path(cls.pwsh).is_file():
            raise unittest.SkipTest('Set G1_TEST_PWSH to a PowerShell executable')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.setup = (
            "Import-Module ([IO.Path]::Combine($PSHOME,'Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1')); "
            "Import-Module ([IO.Path]::Combine($PSHOME,'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1')); "
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def body(self, command):
        prefix = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "'
        self.assertTrue(command.startswith(prefix))
        self.assertTrue(command.endswith('"'))
        body = command[len(prefix):-1]
        # Everything cmd could interpret is inside one quoted argument; none
        # of the remaining text expands in either cmd or the parent PowerShell.
        self.assertFalse(any(c in body for c in ('$', '"', '%', '!', '\n', '\r')))
        self.assertLess(len(command), 8191)
        return body

    def run_body(self, command, extra=''):
        return subprocess.run([self.pwsh, '-NoLogo', '-NoProfile', '-Command',
                               self.setup + extra + self.body(command)],
                              capture_output=True, text=True, timeout=30)

class PortableWindowsCommandTests(Harness):
    def command(self, route='/ok', data=FIXTURE, checksum=None):
        return windows_portable_command(self.url + route, checksum or hashlib.sha256(data).hexdigest())

    def test_http404_stops_before_hash_and_execution(self):
        result = self.run_body(self.command('/404'))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('G1_TEST_INSTALLER_RAN', result.stdout)
        self.assertNotIn('Checksum mismatch', result.stderr)
        self.assertNotIn('Resolve-Path', result.stderr)
        self.assertNotIn('Remove-Item :', result.stderr)

    def test_missing_file_never_executes(self):
        result = self.run_body(self.command(), 'function Invoke-WebRequest {} ; ')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Download did not create a file', result.stderr)
        self.assertNotIn('G1_TEST_INSTALLER_RAN', result.stdout)

    def test_corruption_never_executes(self):
        result = self.run_body(self.command(checksum='0' * 64))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum mismatch', result.stderr)
        self.assertNotIn('G1_TEST_INSTALLER_RAN', result.stdout)

    def test_downloaded_fixture_is_executed_once(self):
        result = self.run_body(self.command())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('G1_TEST_INSTALLER_RAN'), 1)

    def test_installer_exit_code_is_preserved(self):
        result = self.run_body(self.command('/failed', data=FAILED))
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_pasting_in_powershell_preserves_the_entire_argument(self):
        command = self.command()
        capture = (
            "function powershell.exe { $g1Args=@($args); $g1Index=[Array]::IndexOf($g1Args,'-Command'); "
            "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($g1Args[$g1Index+1])) }; "
        )
        result = subprocess.run([self.pwsh, '-NoLogo', '-NoProfile', '-Command', capture + command],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(base64.b64decode(result.stdout.strip()).decode(), self.body(command))


class ShortWindowsCommandTests(Harness):
    """PowerShell-native two-statement form: download, then run as a file."""

    def command(self):
        return windows_short_command(self.url + '/ok')

    def test_short_line_is_one_plain_statement(self):
        command = self.command()
        self.assertTrue(command.startswith('irm '))
        self.assertTrue(command.endswith(' | iex'))
        self.assertNotIn('param(', command)
        self.assertNotIn('[CmdletBinding', command)
        self.assertNotIn('scriptblock', command)
        self.assertLess(len(command), 400)

    def test_downloaded_file_runs_and_keeps_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'G1AcqPlus-setup.ps1'
            download = subprocess.run(
                [self.pwsh, '-NoLogo', '-NoProfile', '-Command',
                 self.setup + "Invoke-WebRequest -Uri '" + self.url + "/failed' -OutFile '" + str(target) + "' -UseBasicParsing"],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(download.returncode, 0, download.stderr)
            result = subprocess.run([self.pwsh, '-NoProfile', '-File', str(target)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(result.stdout.count('G1_TEST_INSTALLER_RAN'), 1)

    def test_missing_file_never_executes(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'G1AcqPlus-setup.ps1'
            download = subprocess.run(
                [self.pwsh, '-NoLogo', '-NoProfile', '-Command',
                 self.setup + "try { Invoke-WebRequest -Uri '" + self.url + "/404' -OutFile '" + str(target) + "' -UseBasicParsing -ErrorAction Stop } catch { exit 1 }"],
                capture_output=True, text=True, timeout=30)
            self.assertNotEqual(download.returncode, 0)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
