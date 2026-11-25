import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import g1plus
import devices
import mumu_mac
import mumu_win
import support
import sponsor
import ldplayer
from contextlib import redirect_stdout
import io


class CatalogTests(unittest.TestCase):
    def test_all_catalog_and_published_targets_included_without_tokens(self):
        data = g1plus.load_products()
        self.assertEqual(sum(r['catalog_seen'] for r in data['products']), 124)
        self.assertEqual(len(data['products']), 135)
        self.assertFalse(any('token' in r for r in data['products']))
        for sku in ('g1.2tb.ai.18months_eft', 'g1.2tb.ai.15months_eft', 'g1.ai.ultra.1month_eft.acq'):
            row = next(r for r in data['products'] if r['id'] == sku)
            self.assertFalse(row['catalog_seen'])
        self.assertFalse(any(r['id'].startswith(('g1.sku.', 'g1.target.', 'g1.pro.annual.test')) for r in data['products']))

    def test_queries_are_batched_and_do_not_open_checkout(self):
        session = Mock()
        session.query.return_value = []
        g1plus.scan(session, g1plus.load_products())
        self.assertEqual(session.query.call_count, 7)
        self.assertTrue(all(len(c.args[0]) <= 20 for c in session.query.call_args_list))
        session.checkout.assert_not_called()

    def test_missing_offer_does_not_fall_back(self):
        session = Mock()
        session.query.return_value = [{'key': 'different', 'product': 'g1.2tb.ai'}]
        with self.assertRaises(support.SetupError):
            g1plus.select_current(session, {'key': 'selected', 'product': 'g1.2tb.ai'})
        session.checkout.assert_not_called()

    def test_default_no_does_not_open_checkout(self):
        row = {'key': 'selected', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'monthly', 'offer': 'trial',
               'phases': [{'price': 'Free', 'period': 'P1Y', 'cycles': 1}]}
        session = Mock()
        session.query.return_value = [row]
        with patch('builtins.input', side_effect=['1', '', '', 'q']):
            g1plus.interactive(session, [row], lambda: [row])
        session.checkout.assert_not_called()

    def test_explicit_yes_opens_selected_offer_only(self):
        row = {'key': 'selected', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'monthly', 'offer': 'trial',
               'phases': [{'price': 'Free', 'period': 'P1Y', 'cycles': 1}]}
        session = Mock()
        session.query.return_value = [row]
        session.checkout.return_value = {'code': 0}
        with patch('builtins.input', side_effect=['1', 'y', '', 'q']):
            g1plus.interactive(session, [row], lambda: [row])
        session.checkout.assert_called_once_with('selected')


class DeviceTests(unittest.TestCase):
    def adb(self, root):
        exe = Path(root) / 'adb.exe'; exe.touch()
        return support.Adb(exe, '127.0.0.1:16416')

    def test_remote_and_invalid_device_ports_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'adb.exe'; exe.touch()
            for serial in ('10.0.0.2:5555', 'emulator-5554', '127.0.0.1:0', '127.0.0.1:99999'):
                with self.assertRaises(support.SetupError): support.Adb(exe, serial)

    def test_su_command_quotes_spaces(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            with patch.object(adb, 'command', return_value=Mock(stdout='', stderr='')) as command:
                adb.shell('su', '-c', 'chmod 755 /data/local/tmp/frida-server')
            self.assertEqual(command.call_args.args, ('shell', "su -c 'chmod 755 /data/local/tmp/frida-server'"))

    def test_google_service_is_not_a_signed_in_account(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            with patch.object(adb, 'shell', return_value='AuthenticatorDescription {type=com.google}'):
                self.assertFalse(adb.account_ready())
            with patch.object(adb, 'shell', return_value='Account {name=example@example.invalid, type=com.google}'):
                self.assertTrue(adb.account_ready())

    def test_mumu_mac_actual_port_has_priority(self):
        self.assertEqual(mumu_mac.adb_port({'currentUsingAdbPort': 16416, 'adbPort': 5555}), 16416)

    def test_mumu_mac_referral_campaign_preserved(self):
        result = mumu_mac.mac_referral_url('Location: https://api.mumuglobal.com/api/dl/win?channel=aff&campaign=143552\r\n')
        self.assertEqual(result, 'https://api.mumuglobal.com/api/dl/mac?channel=aff&campaign=143552')

    def test_windows_root_modifies_one_instance(self):
        manager = Mock()
        manager.exe = Path('MuMuManager.exe')
        manager.setting.side_effect = ['false', 'true']
        manager.info.return_value = {'is_process_started': False}
        with tempfile.TemporaryDirectory() as root:
            mumu_win.enable_root(manager, 4, Path(root))
        self.assertEqual(manager.call.call_args.args, ('setting', '-v', 4, '-k', 'root_permission', '-val', 'true'))

    def test_mumu_unknown_schema_cannot_create_device(self):
        manager = mumu_mac.MuMu(Path('/Applications/Fake.app'))
        with patch.object(manager, 'call', return_value='{"unexpected":"schema"}'):
            with self.assertRaises(RuntimeError): manager.rows()

    def test_windows_exact_manager_path(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'nx_main/MuMuManager.exe'; path.parent.mkdir(); path.touch()
            self.assertEqual(mumu_win.find_manager(root), path.resolve())


class SponsorTests(unittest.TestCase):
    def test_success_opens_once_even_when_catalog_refreshed(self):
        row = {'key': 'one', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'm', 'offer': 'trial'}
        session = Mock()
        session.query.return_value = [row]
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()), \
                patch('g1plus.announce') as announce, patch('builtins.input', return_value=''), \
                patch('g1plus.interactive', side_effect=lambda s, rows, refresh: (refresh(), refresh())):
            g1plus.run_catalog(session, g1plus.load_products(), Path(root), limit=1)
        announce.assert_called_once_with()
        self.assertEqual(session.query.call_count, 3)
        session.checkout.assert_not_called()

    def test_failed_catalog_never_opens_browser(self):
        session = Mock()
        session.query.return_value = []
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()), \
                patch('g1plus.announce') as announce:
            with self.assertRaises(support.SetupError):
                g1plus.run_catalog(session, g1plus.load_products(), Path(root), limit=1)
        announce.assert_not_called()

    def test_scan_only_never_opens_browser(self):
        session = Mock()
        session.query.return_value = [{'key': 'one', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'm', 'offer': 'trial'}]
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()), \
                patch('g1plus.announce') as announce:
            g1plus.run_catalog(session, g1plus.load_products(), Path(root), limit=1, scan_only=True)
        announce.assert_not_called()

    def test_banner_is_full_height_and_fits_common_windows(self):
        for columns, lines in ((80, 24), (100, 30), (160, 40)):
            result = sponsor.banner(columns, lines)
            self.assertIn(sponsor.NOTICE, result)
            self.assertIn('animeyume.online', result)
            self.assertIn('█', result)
            self.assertEqual(len(result.splitlines()), lines - 1)
            self.assertLessEqual(max(map(len, result.splitlines())), columns - 1)

    def test_credit_printed_before_browser_navigation(self):
        output = io.StringIO()
        def opened(url, new):
            self.assertIn(sponsor.NOTICE, output.getvalue())
            self.assertIn('animeyume.online', output.getvalue())
            self.assertEqual(url, 'https://animeyume.online')
            self.assertEqual(new, 2)
            return True
        with redirect_stdout(output), patch('sponsor.webbrowser.open', side_effect=opened) as browser:
            self.assertTrue(sponsor.announce())
        browser.assert_called_once()

    def test_browser_failure_does_not_abort_catalog(self):
        with redirect_stdout(io.StringIO()), patch('sponsor.webbrowser.open', side_effect=OSError):
            self.assertFalse(sponsor.announce())


class LDPlayerTests(unittest.TestCase):
    def test_documented_list_formats_and_quoted_name(self):
        rows = ldplayer.parse_instances('0,Main,0,0,0,-1,-1\n4,"Test, four",1,2,1,100,200,1280,720,240')
        self.assertEqual(list(rows), [0, 4])
        self.assertEqual(rows[4]['name'], 'Test, four')
        self.assertTrue(rows[4]['started'])

    def test_unknown_schema_and_desktop_sentinel_rejected(self):
        for text in ('unexpected output', '99999,Desktop,0,0,1,100,200', '0,Main,1,2,yes,100,200'):
            with self.assertRaises(support.SetupError):
                ldplayer.parse_instances(text)

    def test_console_path_and_requested_device_checked(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'ldconsole.exe'; path.touch()
            self.assertEqual(ldplayer.find_console(root), path.resolve())
        with self.assertRaises(support.SetupError):
            ldplayer.choose({0: {'name': 'Main'}}, 4)

    def test_commands_keep_explicit_index(self):
        console = ldplayer.Console(Path('ldconsole.exe'))
        with patch.object(console, 'instances', return_value={4: {'name': 'Test'}}), \
                patch.object(console, 'call') as call:
            console.selected(4, 'modify', '--root', 1)
        call.assert_called_once_with('modify', '--index', 4, '--root', 1)

    def test_absent_device_is_never_modified(self):
        console = ldplayer.Console(Path('ldconsole.exe'))
        with patch.object(console, 'instances', return_value={0: {'name': 'Main'}}), \
                patch.object(console, 'call') as call:
            with self.assertRaises(support.SetupError):
                console.selected(4, 'modify', '--root', 1)
        call.assert_not_called()

    def test_wrong_port_mapping_stops_before_root(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch()
            (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe)
            with patch('ldplayer.Adb') as cls:
                cls.return_value.shell.return_value = ''
                with self.assertRaises(support.SetupError):
                    ldplayer.connect_verified(console, 4)
            cls.assert_called_once_with(Path(root) / 'adb.exe', '127.0.0.1:5563')
            cls.return_value.root.assert_not_called()

    def test_successful_mapping_verified_through_vendor_console(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch()
            (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe)
            with patch('ldplayer.Adb') as cls, patch('ldplayer.secrets.token_hex', return_value='abc123'):
                cls.return_value.shell.return_value = '/data/local/tmp/g1plus-device-abc123'
                self.assertIs(ldplayer.connect_verified(console, 4), cls.return_value)
            console.selected.assert_called_once_with(4, 'adb', '--command', 'shell touch /data/local/tmp/g1plus-device-abc123')
            cls.return_value.root.assert_not_called()

    def test_referral_is_original_short_link(self):
        self.assertEqual(ldplayer.REFERRAL_URL, 'https://leap.ldplayer.gg/T4WsicN6b')


if __name__ == '__main__':
    unittest.main()
