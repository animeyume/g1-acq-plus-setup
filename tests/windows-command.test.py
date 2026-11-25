"""Run the portable entry point against harmless fixtures, never the installer."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_commands import windows_portable_command, windows_short_command

FIXTURE = b"Write-Output 'G1_TEST_INSTALLER_RAN'\n"
FAILED = b"Write-Output 'G1_TEST_INSTALLER_RAN'\nexit 7\n"


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
    """The short line must behave like the verified one: no run on failure."""

    def command(self, route='/ok', data=FIXTURE, checksum=None):
        return windows_short_command(self.url + route)

    def test_short_line_is_compact_and_shell_safe(self):
        command = self.command()
        self.assertTrue(command.startswith('powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "'))
        self.assertLess(len(command), 500)
        self.assertFalse(any(c in self.body(command) for c in ('"', '%', '!', '\n', '\r')))

    def test_download_failure_never_executes_the_installer(self):
        result = self.run_body(self.command('/404'))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('G1_TEST_INSTALLER_RAN', result.stdout)

    def test_fixture_is_executed_once(self):
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
