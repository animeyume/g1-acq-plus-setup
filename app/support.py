from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import ssl
import subprocess
import time
import urllib.request

PKG = 'com.google.android.apps.subscriptions.red'


class SetupError(RuntimeError):
    pass


def run(args, *, timeout=30, check=True, capture=True):
    result = subprocess.run(list(map(str, args)), capture_output=capture, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    if check and result.returncode:
        raise SetupError((result.stderr or result.stdout or f'Команда завершилась: {result.returncode}')[-1000:])
    return result


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def decode_json(text):
    text = text.lstrip('\ufeff')
    for match in re.finditer(r'[\[{]', text):
        try:
            return json.JSONDecoder().raw_decode(text[match.start():])[0]
        except ValueError:
            continue
    raise SetupError('MuMu вернул неожиданный ответ: ' + text[:300])


def truth(value):
    return value is True or str(value).lower() in ('1', 'true')


def download(url, destination, expected=None):
    import certifi
    if not url.startswith('https://'):
        raise SetupError('Ожидалась HTTPS-ссылка.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + '.part')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'G1AcqPlus-Setup/2.0'})
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, context=context, timeout=60) as response, part.open('wb') as output:
            if not response.url.startswith('https://'):
                raise SetupError('Небезопасное перенаправление загрузки.')
            while block := response.read(1024 * 1024):
                output.write(block)
        if expected and sha256(part) != expected.lower():
            raise SetupError('Контрольная сумма скачанного файла не совпала.')
        part.replace(destination)
    finally:
        part.unlink(missing_ok=True)
    return destination


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def local_adb_serial(serial, allow_emulator=False):
    match = re.fullmatch(r'127\.0\.0\.1:(\d{1,5})', serial)
    if match:
        return 0 < int(match[1]) < 65536
    match = re.fullmatch(r'emulator-(\d{1,5})', serial) if allow_emulator else None
    return bool(match and 0 < int(match[1]) < 65536)


class Adb:
    def __init__(self, exe, serial, *, allow_emulator=False):
        self.exe, self.serial = Path(exe), serial
        if not self.exe.is_file():
            raise SetupError('Не найден ADB: ' + str(self.exe))
        if not local_adb_serial(serial, allow_emulator):
            raise SetupError('Укажите локальный ADB-порт выбранного эмулятора: 127.0.0.1:PORT.')

    def command(self, *args, timeout=30, check=True):
        return run([self.exe, '-s', self.serial, *args], timeout=timeout, check=check)

    def shell(self, *args, timeout=15, check=True):
        result = self.command('shell', shlex.join(list(map(str, args))), timeout=timeout, check=check)
        return (result.stdout + result.stderr).strip()

    def connect(self):
        if self.serial.startswith('127.0.0.1:'):
            run([self.exe, 'connect', self.serial], timeout=10)
        result = self.command('shell', 'getprop sys.boot_completed', timeout=10)
        if result.stdout.strip().splitlines()[-1:] != ['1']:
            raise SetupError('ADB отвечает, но Android ещё не завершил загрузку: ' + (result.stdout.strip() or '(пустой ответ)'))

    def root(self):
        for args in (('su', '-c', 'id'), ('su', '0', 'id')):
            try:
                if re.search(r'\buid=0\b', self.shell(*args)):
                    return True
            except (SetupError, subprocess.SubprocessError):
                pass
        return False

    def account_dump(self):
        """Return (signed-in Google account found, accounts text)."""
        last = ''
        for parts in (('dumpsys', 'account'), ('dumpsys', 'account', '--user', '0'),
                      ('su', '-c', 'dumpsys account'), ('su', '-c', 'dumpsys account --user 0')):
            try:
                text = self.shell(*parts, check=False)
            except (SetupError, subprocess.SubprocessError):
                continue
            last = text or last
            accounts = [line.strip() for line in text.splitlines() if 'Account {' in line]
            if any(re.search(r'type=com\.google', line) for line in accounts):
                return True, '\n'.join(accounts)
        return False, last

    def account_ready(self):
        return self.account_dump()[0]

    def package_paths(self):
        """Locate Google One even when the build reports a variant package id."""
        for parts in (('pm', 'path', PKG), ('su', '-c', 'pm path ' + PKG)):
            try:
                text = self.shell(*parts, check=False)
            except (SetupError, subprocess.SubprocessError):
                continue
            if 'package:' in text:
                return text.strip()
        try:
            text = self.shell('pm', 'list', 'packages', check=False)
        except (SetupError, subprocess.SubprocessError):
            return ''
        matches = [line.strip() for line in text.splitlines()
                   if PKG in line or 'com.google.android.apps.subscriptions' in line]
        return '\n'.join(matches)

    def package_ready(self):
        return bool(self.package_paths())

    def prepare_google(self):
        if self.account_ready() and self.package_ready():
            return
        self.shell('am', 'start', '-a', 'android.intent.action.VIEW', '-d', 'market://details?id=' + PKG,
                   '-p', 'com.android.vending', check=False)
        attempt = 0
        while True:
            attempt += 1
            account, accounts = self.account_dump()
            package = self.package_paths()
            if account and package:
                print('Google Play: аккаунт найден, Google One установлен.', flush=True)
                return
            print('Проверка ' + str(attempt) + ' (ADB ' + self.serial + '): аккаунт Google '
                  + ('найден' if account else 'НЕ найден') + ', Google One '
                  + ('установлен' if package else 'НЕ установлен') + '.', flush=True)
            if not account:
                detail = ' | '.join(accounts.splitlines()[-4:]) if accounts else 'dumpsys account вернул пустой ответ'
                print('  Аккаунты: ' + detail[:300], flush=True)
            if not package:
                print('  Пакет ' + PKG + ' не найден ни через pm path, ни через pm list packages.', flush=True)
            print('В эмуляторе войдите в Google Play, установите Google One и откройте его один раз.', flush=True)
            if input('Enter — проверить снова; Q — выйти: ').strip().lower() == 'q':
                raise SetupError('Остановлено пользователем.')

    def app_pid(self):
        result = self.shell('pidof', PKG, check=False).split()
        return int(result[0]) if result and result[0].isdigit() else None

    def open_google(self):
        self.shell('monkey', '-p', PKG, '-c', 'android.intent.category.LAUNCHER', '1', check=False)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            pid = self.app_pid()
            if pid:
                return pid
            time.sleep(0.5)
        raise SetupError('Google One не запустился. Откройте его вручную и повторите запуск.')
