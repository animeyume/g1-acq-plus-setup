from __future__ import annotations
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import sys
import traceback

from support import Adb, SetupError
from session import BillingSession, prepare_server
from sponsor import announce

APP = Path(__file__).resolve().parent
VERSION = '2.0.6'


def load_products(path=APP / 'products.json'):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    products = data['products']
    if not products or any(not re.fullmatch(r'g1\.[A-Za-z0-9._-]{1,150}', row['id']) for row in products):
        raise SetupError('Некорректный каталог товаров.')
    return data


def period(text):
    if not re.fullmatch(r'P(?:\d+[YMWD])+', text):
        return text
    units = {'Y': 'г.', 'M': 'мес.', 'W': 'нед.', 'D': 'дн.'}
    return ' '.join(number + ' ' + units[unit] for number, unit in re.findall(r'(\d+)([YMWD])', text))


def describe(row):
    result = []
    for phase in row['phases']:
        line = phase['price'] + ' / ' + period(phase['period'])
        if phase['cycles']:
            line += f" × {phase['cycles']}"
        elif phase.get('recurrence') == 1:
            line += ' (далее, с автопродлением)'
        elif phase.get('recurrence') == 3:
            line += ' (без повторения)'
        result.append(line)
    return ' → '.join(result)


def scan(session, catalog, limit=None):
    products = catalog['products'][:limit] if limit else catalog['products']
    found = {}
    total = math.ceil(len(products) / 20)
    for offset in range(0, len(products), 20):
        print(f'Google Play: пакет {offset // 20 + 1}/{total}…', flush=True)
        for row in session.query([x['id'] for x in products[offset:offset + 20]]):
            found[row['key']] = row
    return sorted(found.values(), key=lambda row: (row['title'], row['product'], row['base_plan'], row['offer']))


def select_current(session, selected):
    rows = session.query([selected['product']])
    for row in rows:
        if row['key'] == selected['key']:
            return row
    raise SetupError('Google Play больше не возвращает это предложение. Обновите список.')


def interactive(session, rows, rescan):
    page, search, include_base = 0, '', False
    while True:
        visible = [r for r in rows if (include_base or r['offer']) and
                   (not search or search in (r['title'] + ' ' + r['product']).lower())]
        page = max(0, min(page, max(0, (len(visible) - 1) // 15)))
        print(f'\nG1 ACQ Plus — ответы Google Play | {len(visible)} вариантов | страница {page + 1}/{max(1, math.ceil(len(visible) / 15))}')
        for index in range(page * 15, min((page + 1) * 15, len(visible))):
            row = visible[index]
            print(f"{index + 1:3}. {row['title']} | {row['offer'] or 'базовый тариф'}\n     {describe(row)}")
        print('Номер — проверить вариант; /текст — поиск; n/p — страницы; a — базовые тарифы; r — обновить; q — выход')
        raw = input('> ').strip()
        if raw.lower() == 'q':
            return
        if raw.lower() == 'n':
            page += 1
        elif raw.lower() == 'p':
            page -= 1
        elif raw.lower() == 'a':
            include_base, page = not include_base, 0
        elif raw.lower() == 'r':
            rows, page = rescan(), 0
        elif raw.startswith('/'):
            search, page = raw[1:].lower(), 0
        elif raw.isdigit() and 1 <= int(raw) <= len(visible):
            try:
                row = select_current(session, visible[int(raw) - 1])
                print('\n' + row['title'] + '\n' + row['product'] + '\n' + describe(row))
                print('Окончательные условия и подтверждение подписки — в окне Google Play в эмуляторе.')
                if input('Открыть окно оформления этого варианта? [y/N]: ').strip().lower() in ('y', 'yes', 'д', 'да'):
                    result = session.checkout(row['key'])
                    if result['code'] == 0:
                        print('Окно Google Play открыто в эмуляторе. Скрипт подписку не подтверждает.')
                    else:
                        print('Google Play отказал: ' + str(result['code']) + ' ' + result.get('message', ''))
            except Exception as error:
                print('Не удалось открыть вариант: ' + str(error))
            input('Enter — вернуться к списку: ')


def run_catalog(session, catalog, work, limit=None, scan_only=False):
    print(f"Каталог источника: {catalog['catalog_products']} SKU; всего опубликованных кандидатов: {len(catalog['products'])}.")
    def rescan():
        rows = scan(session, catalog, limit)
        path = work / 'live-offers.json'
        path.write_text(json.dumps({'source_commit': catalog['commit'], 'offers': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Google Play вернул {len(rows)} вариантов. Отчёт: {path}')
        return rows
    rows = rescan()
    if scan_only:
        return
    if not rows:
        raise SetupError('Для этого аккаунта Google Play не вернул предложения из каталога.')
    announce()
    input('Enter — перейти к каталогу предложений: ')
    interactive(session, rows, rescan)


def doctor(work):
    report = {'app_version': VERSION, 'python': sys.version, 'executable': sys.executable,
              'platform': platform.system(), 'machine': platform.machine(), 'packages': {}}
    for name in ('frida', 'frida-tools', 'certifi'):
        try:
            report['packages'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report['packages'][name] = None
    report['candidate_products'] = len(load_products()['products'])
    (work / 'doctor.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='G1 ACQ Plus: live Google Play catalog')
    parser.add_argument('--work', type=Path, default=APP.parent)
    parser.add_argument('--manager')
    parser.add_argument('--emulator', choices=('ldplayer', 'mumu'))
    parser.add_argument('--instance', type=int)
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--scan-only', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--serial', help='diagnostic override; requires --adb and never selects another device')
    parser.add_argument('--adb', type=Path)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if args.doctor:
        doctor(work)
        return 0
    if args.limit is not None and args.limit < 1:
        raise SetupError('--limit должен быть положительным.')
    print('[3.1] Подготовка эмулятора и проверка Android root…', flush=True)
    if args.serial:
        if not args.adb:
            raise SetupError('Для --serial укажите точный путь --adb.')
        adb = Adb(args.adb, args.serial)
        adb.connect()
    else:
        from devices import prepare
        adb = prepare(work, args.manager, args.instance, args.emulator)
    if not adb.root():
        raise SetupError('Android root отсутствует.')
    print('Android root подтверждён: uid=0.', flush=True)
    print('[3.2] Проверка Google Play и Google One…', flush=True)
    adb.prepare_google()
    print('[3.3] Подключение скрипта к Google One…', flush=True)
    import frida
    prepare_server(adb, work, frida.__version__)
    pid = adb.app_pid() or adb.open_google()
    catalog = load_products()
    session = BillingSession(adb, APP)
    try:
        session.attach(pid)
        print('[3.4] Получение каталога предложений Google Play…', flush=True)
        run_catalog(session, catalog, work, args.limit, args.scan_only)
    finally:
        session.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nОстановлено. Подтверждение подписки не выполнялось.')
        raise SystemExit(130)
    except Exception as error:
        print('\nОШИБКА: ' + str(error), file=sys.stderr)
        try:
            target = Path(sys.argv[sys.argv.index('--work') + 1]) if '--work' in sys.argv else APP.parent
            (target / 'last-error.log').write_text(traceback.format_exc(), encoding='utf-8')
            print('Подробности: ' + str(target / 'last-error.log'), file=sys.stderr)
        except Exception:
            pass
        raise SystemExit(1)
