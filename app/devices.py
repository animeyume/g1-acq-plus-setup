from pathlib import Path
import json
import os
import re
import shutil
import ssl
import sys
import urllib.request

from support import Adb, SetupError, run


def install_windows(work):
    import certifi
    folder = work / 'downloads'
    folder.mkdir(exist_ok=True)
    url = 'https://aff.gearupglobal.com/product/download/t65aHKpDb4em'
    print('Скачиваю MuMu по реферальной ссылке…', flush=True)
    with urllib.request.urlopen(url, context=ssl.create_default_context(cafile=certifi.where()), timeout=60) as response:
        name = response.headers.get_filename()
        if not name or not re.fullmatch(r'MuMu[A-Za-z0-9_.()-]*\.exe', name) or not response.url.startswith('https://'):
            raise SetupError('Реферальный сервер вернул неожиданный установщик.')
        path = folder / name
        partial = folder / (name + '.part')
        try:
            with partial.open('wb') as output:
                shutil.copyfileobj(response, output)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', Path(__file__).with_name('check-publisher.ps1'), '-Installer', path], timeout=60)
    os.startfile(str(path))
    input('Завершите установку MuMu в его окне, закройте установщик и нажмите Enter: ')


def windows(work, manager_path=None, instance=None):
    import mumu_win as m
    path = m.find_manager(manager_path)
    if not path:
        if shutil.disk_usage(work).free < 8 * 1024**3:
            raise SetupError('Для новой установки MuMu освободите хотя бы 8 ГиБ.')
        install_windows(work)
        path = m.find_manager()
    if not path:
        path = m.find_manager(input('Укажите папку MuMu или полный путь MuMuManager.exe: ').strip())
    if not path:
        raise SetupError('MuMuManager.exe не найден.')
    manager = m.Manager(path)
    index = m.choose_instance(manager, instance)
    m.enable_root(manager, index, work)
    row = m.wait_android(manager, index)
    adb = Adb(m.find_adb(path), m.local_serial(row))
    adb.connect()
    if not adb.root():
        input('Разрешите root в окне Android MuMu. Затем нажмите Enter: ')
        if not adb.root():
            raise SetupError('MuMu не предоставил root. Проверьте Root в настройках выбранного устройства.')
    (work / 'device.json').write_text(json.dumps({'platform': 'windows', 'manager': str(path), 'index': index}, indent=2))
    return adb


def macos(work, instance=None):
    import mumu_mac as m
    app = m.installed_app() or m.install_mumu(work)
    m.command(['/usr/bin/open', '-a', app])
    manager = m.MuMu(app)
    rows = m.wait_rows(manager)
    if not rows:
        manager.call('create', '--setting', json.dumps({'vmRootEnable': True}))
        rows = manager.rows()
    if not rows:
        raise SetupError('Создайте Android-устройство в MuMu и повторите запуск.')
    index = m.choose(rows, instance)
    exe = m.find_adb(app)
    port = m.adb_port(rows[index])
    adb = None
    if port:
        candidate = Adb(exe, f'127.0.0.1:{port}')
        try:
            candidate.connect()
            if candidate.root():
                adb = candidate
        except Exception:
            pass
    if adb is None:
        snapshot = work / f'mumu-settings-before-{index}.json'
        if not snapshot.exists():
            snapshot.write_text(json.dumps(rows[index], ensure_ascii=False, indent=2))
        print(f'Включаю Android root для устройства MuMu {index} и перезапускаю его.', flush=True)
        manager.call('config', str(index), '--setting', json.dumps({'vmRootEnable': True}))
        try:
            manager.call('restart', str(index))
        except RuntimeError:
            manager.call('open', str(index))
        adb = m.connect_device(manager, index, exe)
        if not adb.root():
            input('Разрешите root в MuMu, затем нажмите Enter: ')
            if not adb.root():
                raise SetupError('Проверка root не прошла. Оформление не запускается.')
    (work / 'device.json').write_text(json.dumps({'platform': 'macos', 'app': str(app), 'index': index}, indent=2))
    return adb


def prepare(work, manager=None, instance=None, emulator=None):
    if sys.platform == 'win32':
        if emulator in (None, 'ldplayer'):
            from ldplayer import prepare as prepare_ldplayer
            return prepare_ldplayer(work, manager, instance)
        return windows(work, manager, instance)
    if sys.platform == 'darwin':
        if emulator == 'ldplayer':
            raise SetupError('LDPlayer не имеет нативной Mac-версии. Для Mac используется MuMu.')
        return macos(work, instance)
    raise SetupError('Поддерживаются Windows x64 и Mac с Apple Silicon.')
