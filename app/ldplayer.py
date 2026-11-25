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

from support import Adb, SetupError

REFERRAL_URL = 'https://leap.ldplayer.gg/T4WsicN6b'


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


def find_console(explicit=None):
    if explicit:
        result = resolve_console(explicit)
        if not result:
            raise SetupError('ldconsole.exe не найден по указанному пути: ' + str(explicit))
        return result
    candidates = registry_folders()
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                 os.environ.get('LOCALAPPDATA'), 'C:/', 'D:/'):
        if base:
            for product in ('LDPlayer/LDPlayer14', 'LDPlayer/LDPlayer9', 'LDPlayer9', 'LDPlayer14',
                            'Changzhi/LDPlayer', 'leidian/LDPlayer9', 'leidian/LDPlayer14'):
                candidates.append(str(Path(base) / product))
    found = list(dict.fromkeys(path for raw in candidates if (path := resolve_console(raw))))
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
        result = subprocess.run([str(self.exe), *map(str, args)], cwd=self.exe.parent,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
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
    while time.monotonic() < deadline:
        row = console.instances().get(index)
        if row is None:
            raise SetupError('Выбранное устройство LDPlayer исчезло.')
        if row['started'] == started:
            return
        time.sleep(2)
    raise SetupError('LDPlayer не завершил запуск/остановку. Проверьте окно эмулятора и повторите запуск.')


def connect_verified(console, index, timeout=90):
    exe = console.exe.parent / 'adb.exe'
    if not exe.is_file():
        raise SetupError('В выбранной папке LDPlayer отсутствует adb.exe.')
    port = 5555 + index * 2
    if port > 65535:
        raise SetupError('Индекс LDPlayer выходит за диапазон локальных портов.')
    adb = Adb(exe, f'127.0.0.1:{port}')
    deadline = time.monotonic() + timeout
    while True:
        try:
            adb.connect()
            break
        except (SetupError, subprocess.SubprocessError):
            if time.monotonic() >= deadline:
                raise SetupError('Локальный ADB LDPlayer недоступен. В настройках включите ADB для локального подключения.')
            time.sleep(2)
    # Prove the documented index-to-port mapping with a fresh marker through
    # the vendor console before executing root commands over this ADB socket.
    marker = '/data/local/tmp/g1plus-device-' + secrets.token_hex(16)
    console.selected(index, 'adb', '--command', 'shell touch ' + marker)
    if adb.shell('ls', marker, check=False).strip() != marker:
        raise SetupError('ADB-порт не подтверждён для выбранного LDPlayer. Закройте конфликтующую версию LDPlayer и повторите запуск.')
    adb.shell('rm', '-f', marker, check=False)
    return adb


def prepare(work, manager=None, instance=None):
    print('Для этой сборки выбирайте LDPlayer 9: 64-битная версия на Android 9.')
    path = find_console(manager)
    if not path:
        print('Открываю официальную партнёрскую страницу загрузки LDPlayer: ' + REFERRAL_URL)
        print('На странице загрузки выберите LDPlayer 9. LDPlayer 14 — бета-версия; для этой сборки рекомендуем 9.')
        try:
            webbrowser.open(REFERRAL_URL, new=2)
        except Exception:
            pass
        input('После установки и первого запуска LDPlayer 9 нажмите Enter: ')
        path = find_console()
        if not path:
            path = find_console(input('Полный путь к папке LDPlayer или ldconsole.exe: ').strip())
    if not path:
        raise SetupError('LDPlayer не установлен.')
    console = Console(path)
    index = choose(console.instances(), instance)
    row = console.instances()[index]
    if not row['started']:
        console.selected(index, 'launch')
        wait_state(console, index, True)
    adb = connect_verified(console, index)
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
        adb = connect_verified(console, index)
        if not adb.root():
            input('В настройках LDPlayer включите ROOT, перезапустите выбранное устройство и нажмите Enter: ')
            adb = connect_verified(console, index)
            if not adb.root():
                raise SetupError('LDPlayer не предоставил su -> uid=0. Каталог не запущен.')
    (work / 'device.json').write_text(json.dumps({'platform': 'windows', 'emulator': 'ldplayer',
                                               'console': str(path), 'index': index}, indent=2), encoding='utf-8')
    return adb
