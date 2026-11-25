"""LDPlayer's documented console; all operations target a selected index."""
import csv
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import webbrowser

from support import Adb, SetupError, local_adb_serial, run

REFERRAL_URL = 'https://leap.ldplayer.gg/T4WsicN6b'


class AdbUnavailable(SetupError):
    pass


def resolve_console(raw):
    folder = Path(os.path.expandvars(str(raw).strip().strip('"')))
    for path in (folder, folder / 'ldconsole.exe', folder / 'dnconsole.exe'):
        if path.is_file() and path.name.lower() in ('ldconsole.exe', 'dnconsole.exe'):
            return path.resolve()
    return None


def registry_folders():
    if os.name != 'nt':
        return []
    import winreg
    result = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view) as keys:
                    for index in range(winreg.QueryInfoKey(keys)[0]):
                        try:
                            with winreg.OpenKey(keys, winreg.EnumKey(keys, index)) as key:
                                name = str(winreg.QueryValueEx(key, 'DisplayName')[0])
                                if not re.search(r'LDPlayer|雷电模拟器', name, re.I):
                                    continue
                                for field in ('InstallLocation', 'UninstallString', 'DisplayIcon'):
                                    try:
                                        value = str(winreg.QueryValueEx(key, field)[0])
                                        if field == 'InstallLocation':
                                            result.append(value)
                                        else:
                                            match = re.match(r'^"([^"]+)"|^(.*?\.exe)', value, re.I)
                                            if match:
                                                result.append(str(Path(match.group(1) or match.group(2)).parent))
                                    except OSError:
                                        pass
                        except OSError:
                            continue
            except OSError:
                continue
    return result


def process_folders():
    if os.name != 'nt':
        return []
    script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Get-CimInstance Win32_Process "
              "-Filter \"Name = 'dnplayer.exe' OR Name = 'LDPlayer.exe' OR Name = 'ldmultiplayer.exe'\" "
              "| Select-Object -ExpandProperty ExecutablePath | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-Command', script],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=8)
        if result.returncode or not result.stdout.strip():
            return []
        data = json.loads(result.stdout.lstrip('\ufeff'))
        return [str(Path(value).parent) for value in (data if isinstance(data, list) else [data])
                if isinstance(value, str) and value]
    except (ValueError, OSError, subprocess.SubprocessError):
        return []


def discover_consoles():
    candidates = registry_folders()
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                 os.environ.get('LOCALAPPDATA'), 'C:/', 'D:/'):
        if base:
            for product in ('LDPlayer/LDPlayer14', 'LDPlayer/LDPlayer9', 'LDPlayer9', 'LDPlayer14',
                            'Changzhi/LDPlayer', 'leidian/LDPlayer9', 'leidian/LDPlayer14'):
                candidates.append(str(Path(base) / product))
    found = list(dict.fromkeys(path for raw in candidates if (path := resolve_console(raw))))
    if not found:
        found = list(dict.fromkeys(path for raw in process_folders() if (path := resolve_console(raw))))
    return found


def choose_console(found):
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        print('Найдено несколько установок LDPlayer:')
        for number, path in enumerate(found, 1):
            print(f'{number}. {path}')
        while True:
            selected = input('Какую использовать? Номер: ').strip()
            if selected.isdigit() and 1 <= int(selected) <= len(found):
                return found[int(selected) - 1]
    return None


def find_console(explicit=None):
    if explicit:
        result = resolve_console(explicit)
        if not result:
            raise SetupError('ldconsole.exe не найден по указанному пути: ' + str(explicit))
        return result
    return choose_console(discover_consoles())


def wait_installation(explicit=None, timeout=600, interval=3):
    deadline = time.monotonic() + timeout
    next_notice = 0
    while True:
        now = time.monotonic()
        if now >= next_notice:
            print(f'Ожидаю LDPlayer: консоль, ADB и первое Android-устройство; осталось {max(0, int(deadline-now))} с.', flush=True)
            next_notice = now + 10
        candidates = [Path(explicit)] if explicit else discover_consoles()
        ready = []
        for path in candidates:
            if not (path.parent / 'adb.exe').is_file():
                continue
            try:
                if Console(path).instances():
                    ready.append(path)
            except (SetupError, OSError, subprocess.SubprocessError):
                continue
        if ready:
            result = choose_console(ready)
            print('LDPlayer найден и готов: ' + str(result), flush=True)
            return result
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def parse_instances(text):
    rows = {}
    for fields in csv.reader(text.lstrip('\ufeff').splitlines()):
        if not fields:
            continue
        if len(fields) not in (7, 10) or not fields[0].isdigit() or fields[4] not in ('0', '1'):
            raise SetupError('Неожиданный ответ LDPlayer list2. Устройства не изменены.')
        index = int(fields[0])
        if not 0 <= index < 99999 or index in rows:
            raise SetupError('Некорректный индекс устройства LDPlayer.')
        rows[index] = {'index': index, 'name': fields[1], 'started': fields[4] == '1'}
    return rows


class Console:
    def __init__(self, path):
        self.exe = Path(path)

    def call(self, *args):
        try:
            result = subprocess.run([str(self.exe), *map(str, args)], cwd=self.exe.parent,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        except subprocess.TimeoutExpired as error:
            raise SetupError('LDPlayer не ответил на команду ' + str(args[0]) + ' за 15 с. Проверьте окно эмулятора.') from error
        if result.returncode:
            raise SetupError('LDPlayer: ' + (result.stderr or result.stdout)[-700:])
        return result.stdout.strip()

    def instances(self):
        return parse_instances(self.call('list2'))

    def selected(self, index, command, *args):
        if index not in self.instances():
            raise SetupError('Выбранное устройство LDPlayer больше не существует.')
        return self.call(command, '--index', index, *args)


def choose(rows, requested=None):
    if not rows:
        raise SetupError('В LDPlayer нет Android-устройств. Создайте одно в LDMultiplayer и повторите запуск.')
    if requested is not None:
        if requested not in rows:
            raise SetupError('Запрошенное устройство LDPlayer отсутствует: ' + str(requested))
        return requested
    if len(rows) == 1:
        return next(iter(rows))
    for index, row in sorted(rows.items()):
        print(f"{index}. {row['name']}")
    while True:
        raw = input('Номер устройства LDPlayer: ').strip()
        if raw.isdigit() and int(raw) in rows:
            return int(raw)


def wait_state(console, index, started, timeout=240):
    deadline = time.monotonic() + timeout
    next_notice = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_notice:
            action = 'запуска' if started else 'остановки'
            print(f'Ожидаю {action} устройства LDPlayer {index}; осталось {max(0, int(deadline-now))} с.', flush=True)
            next_notice = now + 10
        row = console.instances().get(index)
        if row is None:
            raise SetupError('Выбранное устройство LDPlayer исчезло.')
        if row['started'] == started:
            return
        time.sleep(2)
    raise SetupError('LDPlayer не завершил запуск/остановку. Проверьте окно эмулятора и повторите запуск.')


def reserved_tcp_ports():
    """Windows hides Hyper-V/WSL/WinNAT ranges from ordinary sockets (WSAEACCES)."""
    if os.name != 'nt':
        return []
    try:
        result = subprocess.run(['netsh', 'int', 'ipv4', 'show', 'excludedportrange', 'protocol=tcp'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    ranges = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r'\s*(\d{1,5})\s+(\d{1,5})\s*\*?\s*', line)
        if not match:
            continue
        start, end = int(match[1]), int(match[2])
        if 0 < start <= end < 65536:
            ranges.append((start, end))
    return ranges


def port_reserved(port):
    return any(start <= port <= end for start, end in reserved_tcp_ports())


def find_vboxmanage(console):
    folders = [Path(os.environ[name]) for name in ('ProgramFiles', 'ProgramFiles(x86)') if os.environ.get(name)]
    room = console.exe.parent
    folders.extend((room, room.parent, room.parent.parent))
    for folder in folders:
        try:
            candidates = [folder / 'VBoxManage.exe', *sorted(folder.glob('*box*/VBoxManage.exe'))]
        except OSError:
            candidates = [folder / 'VBoxManage.exe']
        for path in candidates:
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
    return None


def running_vms(vbox):
    try:
        result = subprocess.run([str(vbox), 'list', 'runningvms'], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in result.stdout.splitlines():
        match = re.match(r'\s*"([^"]+)"\s*\{', line)
        if match:
            names.append(match[1])
    return names


def vm_for_index(vbox, index):
    expected = 'leidian' + str(index)
    return next((name for name in running_vms(vbox) if name.lower() == expected), None)


def open_adb_forward(console, index, guest=5555, rule='g1adb', attempts=20):
    """Forward a free local port to the guest ADB port of exactly this LDPlayer VM.

    LDPlayer's own bridge (host 5555, and 5555+2*index) cannot bind when Windows
    reserved that port, so bind the VM's guest 5555 to a port Windows left free.
    """
    vbox = find_vboxmanage(console)
    if not vbox:
        return None
    vm = vm_for_index(vbox, index)
    if not vm:
        return None
    blocked = reserved_tcp_ports()
    for offset in range(attempts):
        host = 15555 + index * 2 + offset
        if host > 65535:
            break
        if any(start <= host <= end for start, end in blocked):
            continue
        try:
            subprocess.run([str(vbox), 'controlvm', vm, 'natpf1', 'delete', rule],
                           capture_output=True, timeout=20)
            added = subprocess.run([str(vbox), 'controlvm', vm, 'natpf1', f'{rule},tcp,127.0.0.1,{host},,{guest}'],
                                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        if not added.returncode:
            return host
    return None


def parse_adb_devices(text):
    result = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and local_adb_serial(fields[0], allow_emulator=True) and not any(field.startswith('usb:') for field in fields[2:]):
            result.append({'serial': fields[0], 'state': fields[1]})
    return result


def connect_verified(console, index, timeout=90, diagnostic_path=None):
    exe = console.exe.parent / 'adb.exe'
    if not exe.is_file():
        raise SetupError('В выбранной папке LDPlayer отсутствует adb.exe.')
    port = 5555 + index * 2
    hint = f'127.0.0.1:{port}' if port < 65536 else None
    forward = None
    if hint and port_reserved(port):
        print(f'Порт {port} зарезервирован Windows (Hyper-V/WSL/WinNAT): открываю обходной мост.', flush=True)
        forward = open_adb_forward(console, index)
        if forward:
            hint = f'127.0.0.1:{forward}'
            print('Обходной мост ' + hint + ' → ADB устройства LDPlayer ' + str(index) + '.', flush=True)
            try:
                run([exe, 'disconnect', hint], timeout=10, check=False)
            except (SetupError, OSError, subprocess.SubprocessError):
                pass
    marker = '/data/local/tmp/g1plus-device-' + secrets.token_hex(16)
    deadline = time.monotonic() + timeout
    next_notice = 0
    tcp_tried = False
    report = {'console': str(console.exe), 'index': index, 'devices': [], 'console_adb': '',
              'tcp_response': '', 'last_error': '', 'verified_serial': None}

    def save():
        if diagnostic_path:
            try:
                Path(diagnostic_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError:
                pass

    try:
        while True:
            now = time.monotonic()
            if now >= next_notice:
                print(f'Ищу ADB-устройство LDPlayer {index}; осталось {max(0, int(deadline-now))} с.', flush=True)
                next_notice = now + 10
            try:
                # Let the vendor console address the selected instance first.
                # This can also register its emulator transport with adb.
                report['console_adb'] = console.selected(index, 'adb', '--command', 'shell touch ' + marker)
            except (SetupError, OSError, subprocess.SubprocessError) as error:
                report['console_adb'] = str(error)
            try:
                listed = run([exe, 'devices', '-l'], timeout=10, check=False)
                report['devices_output'] = (listed.stdout + listed.stderr).strip()
                report['devices'] = parse_adb_devices(listed.stdout)
                if listed.returncode:
                    report['last_error'] = report['devices_output']
                online = [row['serial'] for row in report['devices'] if row['state'] == 'device']
                # Only try the legacy default as a hint if adb has no online
                # local transports. A failed TCP hint never hides real devices.
                if not online and hint and not tcp_tried:
                    tcp_tried = True
                    connected = run([exe, 'connect', hint], timeout=10, check=False)
                    report['tcp_response'] = (connected.stdout + connected.stderr).strip()
                    listed = run([exe, 'devices', '-l'], timeout=10, check=False)
                    report['devices_output'] = (listed.stdout + listed.stderr).strip()
                    report['devices'] = parse_adb_devices(listed.stdout)
                    online = [row['serial'] for row in report['devices'] if row['state'] == 'device']
                expected = f'emulator-{5554 + index * 2}'
                online.sort(key=lambda serial: (serial != expected, not serial.startswith('emulator-'), serial))
                for serial in online:
                    candidate = Adb(exe, serial, allow_emulator=True)
                    try:
                        # The vendor console cannot create the marker while the
                        # vendor bridge is blocked, but a rule we created on the
                        # selected VM proves the mapping on its own.
                        if not (forward and serial == hint):
                            marker_reply = candidate.shell('ls', marker, timeout=5, check=False).strip()
                            if marker_reply != marker:
                                report['last_error'] = serial + ': устройство не подтвердило метку выбранного LDPlayer. ' + marker_reply[-250:]
                                continue
                        candidate.connect()
                    except (SetupError, subprocess.SubprocessError) as error:
                        report['last_error'] = serial + ': ' + str(error)
                        continue
                    candidate.shell('rm', '-f', marker, timeout=5, check=False)
                    report['verified_serial'] = serial
                    save()
                    print('Подтверждено устройство выбранного LDPlayer: ' + serial, flush=True)
                    return candidate
            except (SetupError, OSError, subprocess.SubprocessError) as error:
                report['last_error'] = str(error)
            save()
            if time.monotonic() >= deadline:
                states = ', '.join(row['serial'] + '=' + row['state'] for row in report['devices']) or '(список устройств пуст)'
                messages = ['Не удалось подтвердить ADB выбранного LDPlayer. Устройства: ' + states]
                for label, key in (('ADB connect', 'tcp_response'), ('ldconsole adb', 'console_adb'), ('Проверка', 'last_error')):
                    if report.get(key):
                        messages.append(label + ': ' + report[key][-500:])
                if diagnostic_path:
                    messages.append('Отчёт ADB: ' + str(diagnostic_path))
                if port_reserved(port):
                    messages.append('Порт ' + str(port) + ' зарезервирован Windows: запустите командную строку от администратора, выполните'
                                    ' net stop winnat, netsh int ipv4 add excludedportrange protocol=tcp startport=5555 numberofports=1 store=persistent,'
                                    ' net start winnat, затем перезапустите эмулятор.')
                raise AdbUnavailable('\n'.join(messages))
            time.sleep(2)
    finally:
        if not report['verified_serial']:
            try:
                console.selected(index, 'adb', '--command', 'shell rm -f ' + marker)
            except (SetupError, OSError, subprocess.SubprocessError):
                pass


def connect_interactive(console, index, work=None):
    print('Ищу фактическое локальное ADB-устройство выбранного LDPlayer…', flush=True)
    diagnostic_path = Path(work) / 'adb-diagnostic.json' if work else None
    while True:
        try:
            return connect_verified(console, index, timeout=30, diagnostic_path=diagnostic_path)
        except AdbUnavailable as error:
            print(str(error), flush=True)
            print('Оставьте выбранное Android-устройство запущенным. Настройка ADB в LDPlayer: Open local connection.', flush=True)
            if input('Enter — заново найти устройство в этом запуске; Q — выйти: ').strip().lower() == 'q':
                raise SetupError('Проверка ADB остановлена пользователем.') from error


def prepare(work, manager=None, instance=None):
    print('Для этой сборки выбирайте LDPlayer 9: 64-битная версия на Android 9.')
    print('Ищу установленный LDPlayer…', flush=True)
    path = find_console(manager)
    if not path:
        print('Открываю официальную партнёрскую страницу загрузки LDPlayer: ' + REFERRAL_URL)
        print('На странице загрузки выберите LDPlayer 9. LDPlayer 14 — бета-версия; для этой сборки рекомендуем 9.')
        try:
            webbrowser.open(REFERRAL_URL, new=2)
        except Exception:
            pass
        print('Установите и откройте LDPlayer 9. Скрипт обнаружит его автоматически; перезапуск скрипта не требуется.', flush=True)
    path = wait_installation(path, timeout=180 if path else 600)
    while path is None:
        raw = input('LDPlayer ещё не готов. Укажите папку установки, Enter — повторить поиск, Q — выйти: ').strip()
        if raw.lower() == 'q':
            raise SetupError('Ожидание LDPlayer остановлено пользователем.')
        try:
            explicit = find_console(raw) if raw else None
            path = wait_installation(explicit, timeout=120)
        except SetupError as error:
            print(str(error), flush=True)
    console = Console(path)
    print('Получаю список устройств LDPlayer…', flush=True)
    index = choose(console.instances(), instance)
    row = console.instances()[index]
    if not row['started']:
        console.selected(index, 'launch')
        wait_state(console, index, True)
    adb = connect_interactive(console, index, work)
    print('Проверяю права root выбранного устройства…', flush=True)
    if not adb.root():
        snapshot = work / f'ldplayer-before-{index}.json'
        if not snapshot.exists():
            snapshot.write_text(json.dumps({'console': str(path), 'instance': row}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Включаю штатный Android root только для LDPlayer {index}; это устройство будет перезапущено.')
        console.selected(index, 'quit')
        wait_state(console, index, False, timeout=60)
        console.selected(index, 'modify', '--root', 1)
        console.selected(index, 'launch')
        wait_state(console, index, True)
        adb = connect_interactive(console, index, work)
        if not adb.root():
            input('В настройках LDPlayer включите ROOT, перезапустите выбранное устройство и нажмите Enter: ')
            adb = connect_interactive(console, index, work)
            if not adb.root():
                raise SetupError('LDPlayer не предоставил su -> uid=0. Каталог не запущен.')
    (work / 'device.json').write_text(json.dumps({'platform': 'windows', 'emulator': 'ldplayer',
                                               'console': str(path), 'index': index}, indent=2), encoding='utf-8')
    return adb
