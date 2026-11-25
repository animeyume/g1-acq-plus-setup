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

    def test_offers_are_grouped_and_sorted_best_first(self):
        rows = [
            {'key': 'b', 'product': 'g1.100gb', 'title': '100 GB (Google One)', 'base_plan': 'm', 'offer': 'introprice',
             'phases': [{'price': '1,99 €', 'period': 'P1M', 'cycles': 0}]},
            {'key': 'a', 'product': 'g1.100gb', 'title': '100 GB (Google One)', 'base_plan': 'm', 'offer': 'freetrial',
             'phases': [{'price': 'Бесплатно', 'period': 'P1Y', 'cycles': 1}]},
            {'key': 'c', 'product': 'g1.2tb.ai', 'title': 'Google AI Pro (5 TB)', 'base_plan': 'm', 'offer': 'introprice',
             'phases': [{'price': '0,99 €', 'period': 'P3M', 'cycles': 1}]},
        ]
        grouped = g1plus.group_offers(rows)
        self.assertEqual(sorted(grouped), ['100 GB (Google One)', 'Google AI Pro (5 TB)'])
        self.assertEqual([row['key'] for row in grouped['100 GB (Google One)']], ['a', 'b'])
        self.assertEqual(len(grouped['Google AI Pro (5 TB)']), 1)

    def test_missing_offer_does_not_fall_back(self):
        session = Mock()
        session.query.return_value = [{'key': 'different', 'product': 'g1.2tb.ai'}]
        with self.assertRaises(support.SetupError):
            g1plus.select_current(session, {'key': 'selected', 'product': 'g1.2tb.ai'})
        session.checkout.assert_not_called()

    def test_number_starts_checkout_without_extra_confirmation(self):
        row = {'key': 'selected', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'monthly', 'offer': 'trial',
               'phases': [{'price': 'Free', 'period': 'P1Y', 'cycles': 1}]}
        session = Mock()
        session.query.return_value = [row]
        session.checkout.return_value = {'code': 0}
        with patch('builtins.input', side_effect=['1', '', 'q']), patch('g1plus.visit') as visit, \
                patch('g1plus.credit') as credit:
            g1plus.interactive(session, [row], lambda: [row])
        session.checkout.assert_called_once_with('selected')
        visit.assert_called_once_with()
        credit.assert_called_once_with()

    def test_explicit_yes_opens_selected_offer_only(self):
        row = {'key': 'selected', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'monthly', 'offer': 'trial',
               'phases': [{'price': 'Free', 'period': 'P1Y', 'cycles': 1}]}
        session = Mock()
        session.query.return_value = [row]
        session.checkout.return_value = {'code': 0}
        with patch('builtins.input', side_effect=['1', '', 'q']), patch('g1plus.visit') as visit, \
                patch('g1plus.credit') as credit:
            g1plus.interactive(session, [row], lambda: [row])
        session.checkout.assert_called_once_with('selected')
        visit.assert_called_once_with()
        credit.assert_called_once_with()

    def test_refused_checkout_keeps_site_closed(self):
        row = {'key': 'selected', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'monthly', 'offer': 'trial',
               'phases': [{'price': 'Free', 'period': 'P1Y', 'cycles': 1}]}
        session = Mock()
        session.query.return_value = [row]
        session.checkout.return_value = {'code': 5, 'message': 'declined'}
        session.open_store_page.return_value = 'https://play.google.com/...'
        with patch('builtins.input', side_effect=['1', '', 'q']), patch('g1plus.visit') as visit, \
                patch('g1plus.credit') as credit:
            g1plus.interactive(session, [row], lambda: [row])
        visit.assert_not_called()
        credit.assert_not_called()
        session.open_store_page.assert_called_once_with('g1.2tb.ai')


class DeviceTests(unittest.TestCase):
    def adb(self, root):
        exe = Path(root) / 'adb.exe'; exe.touch()
        return support.Adb(exe, '127.0.0.1:16416')

    def test_remote_and_invalid_device_ports_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'adb.exe'; exe.touch()
            for serial in ('10.0.0.2:5555', 'emulator-5554', '127.0.0.1:0', '127.0.0.1:99999'):
                with self.assertRaises(support.SetupError): support.Adb(exe, serial)

    def test_named_emulator_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'adb.exe'; exe.touch()
            self.assertEqual(support.Adb(exe, 'emulator-5554', allow_emulator=True).serial, 'emulator-5554')
            for serial in ('USB12345', '10.0.0.2:5555', 'emulator-0', 'emulator-65536'):
                with self.assertRaises(support.SetupError):
                    support.Adb(exe, serial, allow_emulator=True)

    def test_named_emulator_connect_skips_tcp_and_ignores_stderr_warnings(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'adb.exe'; exe.touch()
            adb = support.Adb(exe, 'emulator-5554', allow_emulator=True)
            with patch('support.run', return_value=Mock(stdout='1\r\n', stderr='daemon warning')) as run:
                adb.connect()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1:3], ['-s', 'emulator-5554'])
        self.assertNotIn('connect', run.call_args.args[0])

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


class GoogleReadyTests(unittest.TestCase):
    """The Google Play gate must survive odd ROM output and say what is missing."""

    def adb(self, root):
        exe = Path(root) / 'adb.exe'
        exe.touch()
        return support.Adb(exe, '127.0.0.1:15555')

    def test_account_found_in_plain_dumpsys(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            with patch.object(adb, 'shell', return_value='Accounts: 1\n  Account {name=user@gmail.com, type=com.google}'):
                self.assertTrue(adb.account_ready())

    def test_account_falls_back_to_su_variant(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            replies = ['', '', 'Account {name=user@gmail.com, type=com.google}']
            with patch.object(adb, 'shell', side_effect=replies):
                found, text = adb.account_dump()
            self.assertTrue(found)
            self.assertIn('user@gmail.com', text)

    def test_missing_account_reports_raw_answer(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            with patch.object(adb, 'shell', return_value='Accounts: 0'):
                found, text = adb.account_dump()
            self.assertFalse(found)
            self.assertIn('Accounts: 0', text)

    def test_package_found_through_pm_list_variant(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            replies = ['', '', 'package:com.google.android.apps.subscriptions.red\npackage:com.android.vending']
            with patch.object(adb, 'shell', side_effect=replies):
                found = adb.package_paths()
            self.assertIn('subscriptions.red', found)

    def test_prepare_google_returns_when_both_found(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            with patch.object(adb, 'account_dump', return_value=(True, 'Account {type=com.google}')), \
                    patch.object(adb, 'package_paths', return_value='package:/data/app/x/base.apk'), \
                    patch('builtins.input') as prompt, redirect_stdout(io.StringIO()):
                adb.prepare_google()
            prompt.assert_not_called()

    def test_prepare_google_prints_missing_piece(self):
        with tempfile.TemporaryDirectory() as root:
            adb = self.adb(root)
            output = io.StringIO()
            with patch.object(adb, 'account_dump', return_value=(False, 'Accounts: 0')), \
                    patch.object(adb, 'package_paths', return_value='package:com.google.android.apps.subscriptions.red'), \
                    patch.object(adb, 'shell', return_value=''), \
                    patch('builtins.input', side_effect=['', 'q']), \
                    redirect_stdout(output):
                with self.assertRaises(support.SetupError):
                    adb.prepare_google()
            text = output.getvalue()
            self.assertIn('аккаунт Google НЕ найден', text)
            self.assertIn('Google One установлен', text)


class SponsorTests(unittest.TestCase):
    def setUp(self):
        sponsor._visited = False

    def test_catalog_shows_no_sponsor_screens(self):
        row = {'key': 'one', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'm', 'offer': 'trial'}
        session = Mock()
        session.query.return_value = [row]
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()), \
                patch('g1plus.credit') as credit, patch('g1plus.visit') as visit, \
                patch('builtins.input', return_value=''), \
                patch('g1plus.interactive', side_effect=lambda s, rows, refresh: (refresh(), refresh())):
            g1plus.run_catalog(session, g1plus.load_products(), Path(root), limit=1)
        credit.assert_not_called()
        visit.assert_not_called()
        self.assertEqual(session.query.call_count, 3)
        session.checkout.assert_not_called()

    def test_failed_catalog_shows_no_credit_and_no_site(self):
        session = Mock()
        session.query.return_value = []
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()), \
                patch('g1plus.credit') as credit, patch('g1plus.visit') as visit:
            with self.assertRaises(support.SetupError):
                g1plus.run_catalog(session, g1plus.load_products(), Path(root), limit=1)
        credit.assert_not_called()
        visit.assert_not_called()

    def test_scan_only_shows_no_credit_and_no_site(self):
        session = Mock()
        session.query.return_value = [{'key': 'one', 'product': 'g1.2tb.ai', 'title': 'Pro', 'base_plan': 'm', 'offer': 'trial'}]
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()), \
                patch('g1plus.credit') as credit, patch('g1plus.visit') as visit:
            g1plus.run_catalog(session, g1plus.load_products(), Path(root), limit=1, scan_only=True)
        credit.assert_not_called()
        visit.assert_not_called()

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
            console = Mock(exe=exe); console.selected.return_value = ''
            listed = Mock(stdout='List of devices attached\nemulator-5562 device\n', stderr='', returncode=0)
            with patch('ldplayer.Adb') as cls, patch('ldplayer.run', return_value=listed):
                cls.return_value.shell.return_value = ''
                with self.assertRaises(support.SetupError):
                    ldplayer.connect_verified(console, 4, timeout=0)
            cls.assert_called_once_with(Path(root) / 'adb.exe', 'emulator-5562', allow_emulator=True)
            cls.return_value.root.assert_not_called()

    def test_successful_mapping_verified_through_vendor_console(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch()
            (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe); console.selected.return_value = ''
            listed = Mock(stdout='List of devices attached\nemulator-5562 device\n', stderr='', returncode=0)
            with patch('ldplayer.Adb') as cls, patch('ldplayer.secrets.token_hex', return_value='abc123'), \
                    patch('ldplayer.run', return_value=listed):
                cls.return_value.shell.return_value = '/data/local/tmp/g1plus-device-abc123'
                self.assertIs(ldplayer.connect_verified(console, 4), cls.return_value)
            console.selected.assert_called_once_with(4, 'adb', '--command', 'shell touch /data/local/tmp/g1plus-device-abc123')
            cls.return_value.root.assert_not_called()

    def test_device_listing_excludes_usb_and_remote_connections(self):
        rows = ldplayer.parse_adb_devices('List of devices attached\nUSB123 device usb:1-2\nemulator-5554 device usb:1-2\n10.1.0.5:5555 device\nemulator-5556 device product:test\n127.0.0.1:6000 offline\n')
        self.assertEqual(rows, [{'serial': 'emulator-5556', 'state': 'device'}, {'serial': '127.0.0.1:6000', 'state': 'offline'}])

    def test_actual_device_is_selected_by_vendor_marker_not_port_formula(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch(); (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe); console.selected.return_value = ''
            listed = Mock(stdout='emulator-5554 device\nemulator-5580 device\n', stderr='', returncode=0)
            wrong, correct = Mock(), Mock()
            wrong.shell.return_value = 'No such file'
            correct.shell.return_value = '/data/local/tmp/g1plus-device-marker'
            with patch('ldplayer.run', return_value=listed), patch('ldplayer.Adb', side_effect=[wrong, correct]), \
                    patch('ldplayer.secrets.token_hex', return_value='marker'), redirect_stdout(io.StringIO()):
                self.assertIs(ldplayer.connect_verified(console, 0, timeout=0), correct)
            wrong.connect.assert_not_called()
            wrong.root.assert_not_called(); correct.root.assert_not_called()

    def test_failed_tcp_hint_does_not_hide_a_registered_emulator(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch(); (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe); console.selected.return_value = ''
            results = [Mock(stdout='List of devices attached\n', stderr='', returncode=0),
                       Mock(stdout='', stderr='connect refused 10061', returncode=1),
                       Mock(stdout='emulator-5554 device\n', stderr='', returncode=0)]
            with patch('ldplayer.run', side_effect=results), patch('ldplayer.Adb') as cls, \
                    patch('ldplayer.secrets.token_hex', return_value='marker'), redirect_stdout(io.StringIO()):
                cls.return_value.shell.return_value = '/data/local/tmp/g1plus-device-marker'
                self.assertIs(ldplayer.connect_verified(console, 0, timeout=0), cls.return_value)
                cls.assert_called_once_with(Path(root) / 'adb.exe', 'emulator-5554', allow_emulator=True)

    def test_empty_list_preserves_real_tcp_error_in_local_report(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch(); (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe); console.selected.return_value = 'no devices/emulators found'
            report = Path(root) / 'diagnostic.json'
            results = [Mock(stdout='List of devices attached\n', stderr='', returncode=0),
                       Mock(stdout='', stderr='connect refused 10061', returncode=1),
                       Mock(stdout='List of devices attached\n', stderr='', returncode=0)]
            with patch('ldplayer.run', side_effect=results), patch('ldplayer.Adb') as cls, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ldplayer.AdbUnavailable, '10061'):
                    ldplayer.connect_verified(console, 0, timeout=0, diagnostic_path=report)
                cls.assert_not_called()
            saved = json.loads(report.read_text())
            self.assertIn('10061', saved['tcp_response'])
            self.assertIsNone(saved['verified_serial'])

    def test_offline_device_is_not_probed_or_promoted_to_online(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch(); (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe); console.selected.return_value = ''
            listed = Mock(stdout='emulator-5554 offline\n', stderr='', returncode=0)
            with patch('ldplayer.run', return_value=listed), patch('ldplayer.Adb') as cls, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ldplayer.AdbUnavailable, 'offline'):
                    ldplayer.connect_verified(console, 0, timeout=0)
                cls.assert_not_called()

    def test_referral_is_original_short_link(self):
        self.assertEqual(ldplayer.REFERRAL_URL, 'https://leap.ldplayer.gg/T4WsicN6b')

    def test_installation_waits_for_registration_and_first_device(self):
        elapsed = [0.0]
        scans = [0]
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch()
            def discover():
                scans[0] += 1
                if scans[0] == 1:
                    return []
                (Path(root) / 'adb.exe').touch()
                return [exe]
            def sleep(seconds):
                elapsed[0] += seconds
            with patch('ldplayer.discover_consoles', side_effect=discover), \
                    patch('ldplayer.Console') as cls, patch('ldplayer.time.monotonic', side_effect=lambda: elapsed[0]), \
                    patch('ldplayer.time.sleep', side_effect=sleep), patch('builtins.input') as prompt, \
                    redirect_stdout(io.StringIO()):
                cls.return_value.instances.side_effect = [{}, {4: {'name': 'Ready'}}]
                self.assertEqual(ldplayer.wait_installation(timeout=30), exe)
                cls.return_value.selected.assert_not_called()
                prompt.assert_not_called()
        self.assertEqual(scans[0], 3)

    def test_partial_install_does_not_run_console_or_root(self):
        elapsed = [0.0]
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'; exe.touch()
            def sleep(seconds): elapsed[0] += seconds
            with patch('ldplayer.Console') as cls, \
                    patch('ldplayer.time.monotonic', side_effect=lambda: elapsed[0]), \
                    patch('ldplayer.time.sleep', side_effect=sleep), redirect_stdout(io.StringIO()):
                self.assertIsNone(ldplayer.wait_installation(exe, timeout=5, interval=3))
                cls.assert_not_called()

    def test_adb_failure_relaunches_device_without_prompting(self):
        expected = Mock()
        console = Mock()
        with patch('ldplayer.connect_verified', side_effect=[ldplayer.AdbUnavailable('disabled'), expected]) as connect, \
                patch('builtins.input') as prompt, redirect_stdout(io.StringIO()):
            self.assertIs(ldplayer.connect_interactive(console, 4), expected)
        self.assertEqual(connect.call_count, 2)
        console.selected.assert_called_once_with(4, 'launch')
        prompt.assert_not_called()

    def test_adb_prompts_only_after_relaunch_also_fails(self):
        expected = Mock()
        console = Mock()
        with patch('ldplayer.connect_verified',
                   side_effect=[ldplayer.AdbUnavailable('a'), ldplayer.AdbUnavailable('b'), expected]) as connect, \
                patch('builtins.input', return_value='') as prompt, redirect_stdout(io.StringIO()):
            self.assertIs(ldplayer.connect_interactive(console, 4), expected)
        self.assertEqual(connect.call_count, 3)
        self.assertEqual(prompt.call_count, 1)

    def test_adb_retry_does_not_hide_wrong_device_mapping(self):
        with patch('ldplayer.connect_verified', side_effect=support.SetupError('wrong device')), \
                patch('builtins.input') as prompt, redirect_stdout(io.StringIO()):
            with self.assertRaises(support.SetupError):
                ldplayer.connect_interactive(Mock(), 4)
        prompt.assert_not_called()

    def test_adb_wait_can_be_cancelled_without_destructive_commands(self):
        console = Mock()
        with patch('ldplayer.connect_verified', side_effect=ldplayer.AdbUnavailable('disabled')), \
                patch('builtins.input', return_value='q'), redirect_stdout(io.StringIO()):
            with self.assertRaises(support.SetupError):
                ldplayer.connect_interactive(console, 4)
        used = [call.args[1] for call in console.selected.call_args_list]
        self.assertNotIn('quit', used)
        self.assertNotIn('modify', used)

    def test_start_instance_relaunches_after_invalid_client_class(self):
        console = Mock()
        with patch('ldplayer.wait_state', side_effect=[support.SetupError('timeout'), None]) as wait, \
                patch('ldplayer.time.sleep'), redirect_stdout(io.StringIO()):
            ldplayer.start_instance(console, 0)
        self.assertEqual(console.selected.call_count, 2)
        self.assertEqual(wait.call_count, 2)

    def test_start_instance_gives_repair_hint_after_three_attempts(self):
        console = Mock()
        with patch('ldplayer.wait_state', side_effect=support.SetupError('нет устройства')), \
                patch('ldplayer.time.sleep'), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(support.SetupError, 'Исправить'):
                ldplayer.start_instance(console, 0)
        self.assertEqual(console.selected.call_count, 3)

    def test_start_instance_survives_failed_launch_command(self):
        console = Mock()
        console.selected.side_effect = [support.SetupError('COM error'), '', '']
        with patch('ldplayer.wait_state', return_value=None) as wait, \
                patch('ldplayer.time.sleep'), redirect_stdout(io.StringIO()):
            ldplayer.start_instance(console, 0)
        self.assertEqual(wait.call_count, 1)

    def test_console_timeout_has_actionable_error(self):
        with patch('ldplayer.subprocess.run', side_effect=subprocess.TimeoutExpired('ldconsole', 15)):
            with self.assertRaisesRegex(support.SetupError, '15 с'):
                ldplayer.Console('ldconsole.exe').instances()


class LDPlayerBridgeTests(unittest.TestCase):
    """The script must reach ADB on its own, without admin netsh or manual steps."""

    NETSH = ('Protocol tcp Port Exclusion Ranges\n\n'
             'Start Port    End Port\n'
             '----------    --------\n'
             '      2892        2991\n'
             '      5544        5643\n'
             '     50000        50059     *\n')

    def console(self, root):
        exe = Path(root) / 'ldconsole.exe'
        exe.touch()
        (Path(root) / 'adb.exe').touch()
        return Mock(exe=exe)

    def test_excluded_ranges_parsed_from_netsh_output(self):
        with patch.object(ldplayer.os, 'name', 'nt'), \
                patch('ldplayer.subprocess.run', return_value=Mock(stdout=self.NETSH, returncode=0)):
            self.assertEqual(ldplayer.reserved_tcp_ports(), [(2892, 2991), (5544, 5643), (50000, 50059)])
            self.assertFalse(ldplayer.port_reserved(15555))
        with patch.object(ldplayer.os, 'name', 'nt'), \
                patch('ldplayer.subprocess.run', return_value=Mock(stdout='', returncode=1)):
            self.assertEqual(ldplayer.reserved_tcp_ports(), [])

    def test_own_bridge_is_used_without_vendor_console(self):
        with tempfile.TemporaryDirectory() as root:
            console = self.console(root)
            with patch('ldplayer.open_adb_forward', return_value=15555) as forward, \
                    patch('ldplayer.Adb') as cls, redirect_stdout(io.StringIO()):
                self.assertIs(ldplayer.connect_verified(console, 0), cls.return_value)
            forward.assert_called_once_with(console, 0)
            cls.assert_called_once_with(Path(root) / 'adb.exe', '127.0.0.1:15555', allow_emulator=True)
            console.selected.assert_not_called()

    def test_own_bridge_wins_over_foreign_device_on_another_port(self):
        # The operator may already have a working adb2 rule on 15555; our own
        # rule lands on the next free port and must be preferred.
        with tempfile.TemporaryDirectory() as root:
            console = self.console(root)
            listed = Mock(stdout='List of devices attached\n127.0.0.1:15555\tdevice\n', stderr='', returncode=0)
            with patch('ldplayer.open_adb_forward', return_value=15556), \
                    patch('ldplayer.run', return_value=listed), \
                    patch('ldplayer.Adb') as cls, redirect_stdout(io.StringIO()):
                self.assertIs(ldplayer.connect_verified(console, 0), cls.return_value)
            cls.assert_called_once_with(Path(root) / 'adb.exe', '127.0.0.1:15556', allow_emulator=True)
            console.selected.assert_not_called()

    def test_without_vboxmanage_vendor_mapping_is_still_verified_by_marker(self):
        with tempfile.TemporaryDirectory() as root:
            console = self.console(root)
            marker = '/data/local/tmp/g1plus-device-abc123'
            listed = Mock(stdout='List of devices attached\n127.0.0.1:5555\tdevice\n', stderr='', returncode=0)
            with patch('ldplayer.open_adb_forward', return_value=None), \
                    patch('ldplayer.secrets.token_hex', return_value='abc123'), \
                    patch('ldplayer.run', return_value=listed), \
                    patch('ldplayer.Adb') as cls, redirect_stdout(io.StringIO()):
                cls.return_value.shell.return_value = marker
                self.assertIs(ldplayer.connect_verified(console, 0), cls.return_value)
            console.selected.assert_called_once_with(0, 'adb', '--command', 'shell touch ' + marker)

    def test_forward_targets_only_the_selected_instance(self):
        with patch('ldplayer.find_vboxmanage', return_value=Path('VBoxManage.exe')), \
                patch('ldplayer.running_vms', return_value=['leidian0', 'leidian1']):
            self.assertIsNone(ldplayer.vm_for_index(Path('VBoxManage.exe'), 4))
            self.assertEqual(ldplayer.vm_for_index(Path('VBoxManage.exe'), 1), 'leidian1')

    def test_forward_skips_reserved_and_failed_host_ports(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(list(args))
            return Mock(returncode=0 if 'natpf1' in args else 1)

        with patch('ldplayer.find_vboxmanage', return_value=Path('VBoxManage.exe')), \
                patch('ldplayer.running_vms', return_value=['leidian0']), \
                patch('ldplayer.reserved_tcp_ports', return_value=[(15555, 15555)]), \
                patch('ldplayer.subprocess.run', side_effect=fake_run):
            self.assertEqual(ldplayer.open_adb_forward(Path('ldconsole.exe'), 0), 15556)
        self.assertIn(['VBoxManage.exe', 'controlvm', 'leidian0', 'natpf1', 'g1adb,tcp,127.0.0.1,15556,,5555'],
                      [call for call in calls if call[-1].startswith('g1adb,tcp')])

    def test_forward_needs_vboxmanage_and_running_vm(self):
        with patch('ldplayer.find_vboxmanage', return_value=None):
            self.assertIsNone(ldplayer.open_adb_forward(Path('ldconsole.exe'), 0))
        with patch('ldplayer.find_vboxmanage', return_value=Path('VBoxManage.exe')), \
                patch('ldplayer.running_vms', return_value=[]):
            self.assertIsNone(ldplayer.open_adb_forward(Path('ldconsole.exe'), 0))


class LDPlayerRobustnessTests(unittest.TestCase):
    """Different LDPlayer builds and half-started VMs must not abort the run."""

    def test_list2_accepts_extra_columns(self):
        rows = ldplayer.parse_instances('0,Main,1,2,1,100,200,1280,720,240,7,9\n1,Second,0,0,0,-1,-1\n')
        self.assertEqual(sorted(rows), [0, 1])
        self.assertTrue(rows[0]['started'])
        self.assertFalse(rows[1]['started'])

    def test_running_status_two_is_running(self):
        rows = ldplayer.parse_instances('0,Main,0,0,2,-1,-1\n1,Second,0,0,1,10,20\n2,Third,0,0,0,-1,-1\n')
        self.assertTrue(rows[0]['started'])
        self.assertTrue(rows[1]['started'])
        self.assertFalse(rows[2]['started'])

    def test_unexpected_list2_includes_raw_text(self):
        with self.assertRaisesRegex(support.SetupError, 'service is starting'):
            ldplayer.parse_instances('service is starting, try later')

    def test_instances_fall_back_to_list_and_runninglist(self):
        console = ldplayer.Console(Path('ldconsole.exe'))
        with patch.object(console, 'call', side_effect=['garbage', '0,Main\n1,Second\n', '1\n']):
            rows = console.instances()
        self.assertEqual(sorted(rows), [0, 1])
        self.assertFalse(rows[0]['started'])
        self.assertTrue(rows[1]['started'])

    def test_fallback_rejects_inconsistent_runninglist(self):
        console = ldplayer.Console(Path('ldconsole.exe'))
        with patch.object(console, 'call', side_effect=['garbage', '0,Main\n', '5\n']):
            with self.assertRaisesRegex(support.SetupError, 'list2'):
                console.instances()

    def test_wait_state_survives_transient_console_errors(self):
        clock = [0.0]

        def mono():
            clock[0] += 1.0
            return clock[0]

        console = Mock()
        console.instances.side_effect = [support.SetupError('starting'), {0: {'started': True}}]
        with patch('ldplayer.time.monotonic', side_effect=mono), patch('ldplayer.time.sleep'), \
                redirect_stdout(io.StringIO()):
            ldplayer.wait_state(console, 0, True, timeout=30)
        self.assertEqual(console.instances.call_count, 2)

    def test_wait_state_timeout_reports_last_console_answer(self):
        clock = [0.0]

        def mono():
            clock[0] += 1.0
            return clock[0]

        console = Mock()
        console.instances.side_effect = support.SetupError('service is starting')
        with patch('ldplayer.time.monotonic', side_effect=mono), patch('ldplayer.time.sleep'), \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(support.SetupError, 'service is starting'):
                ldplayer.wait_state(console, 0, True, timeout=3)

    def test_offline_transport_is_disconnected_before_retry(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(list(map(str, args)))
            return Mock(stdout='', stderr='', returncode=0)

        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'ldconsole.exe'
            exe.touch()
            (Path(root) / 'adb.exe').touch()
            console = Mock(exe=exe)
            with patch('ldplayer.open_adb_forward', return_value=15555), \
                    patch('ldplayer.run', side_effect=fake_run), patch('ldplayer.time.sleep'), \
                    patch('ldplayer.Adb') as cls, redirect_stdout(io.StringIO()):
                cls.return_value.connect.side_effect = [support.SetupError('device offline'), None]
                self.assertIs(ldplayer.connect_verified(console, 0), cls.return_value)
        self.assertIn([str(Path(root) / 'adb.exe'), 'disconnect', '127.0.0.1:15555'], calls)


if __name__ == '__main__':
    unittest.main()
