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


class Adb:
    def __init__(self, exe, serial):
        self.exe, self.serial = Path(exe), serial
        if not self.exe.is_file():
            raise SetupError('Не найден ADB: ' + str(self.exe))
        if not re.fullmatch(r'127\.0\.0\.1:\d{1,5}', serial) or not 0 < int(serial.rsplit(':', 1)[1]) < 65536:
            raise SetupError('Укажите локальный ADB-порт выбранного эмулятора: 127.0.0.1:PORT.')

    def command(self, *args, timeout=30, check=True):
        return run([self.exe, '-s', self.serial, *args], timeout=timeout, check=check)

    def shell(self, *args, timeout=15, check=True):
        result = self.command('shell', shlex.join(list(map(str, args))), timeout=timeout, check=check)
        return (result.stdout + result.stderr).strip()

    def connect(self):
        run([self.exe, 'connect', self.serial], timeout=15)
        if self.shell('getprop', 'sys.boot_completed') != '1':
            raise SetupError('Android ещё не загрузился или локальный ADB отключён.')

    def root(self):
        for args in (('su', '-c', 'id'), ('su', '0', 'id')):
            try:
                if re.search(r'\buid=0\b', self.shell(*args)):
                    return True
            except (SetupError, subprocess.SubprocessError):
                pass
        return False

    def account_ready(self):
        for parts in (('dumpsys', 'account'), ('su', '-c', 'dumpsys account')):
            text = self.shell(*parts, check=False)
            if re.search(r'Account \{name=[^,}]+,\s*type=com\.google', text):
                return True
        return False

    def package_ready(self):
        return 'package:' in self.shell('pm', 'path', PKG, check=False)

    def prepare_google(self):
        if self.account_ready() and self.package_ready():
            return
        self.shell('am', 'start', '-a', 'android.intent.action.VIEW', '-d', 'market://details?id=' + PKG,
                   '-p', 'com.android.vending', check=False)
        while not (self.account_ready() and self.package_ready()):
            print('В эмуляторе войдите в Google Play, установите Google One и откройте его один раз.')
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
