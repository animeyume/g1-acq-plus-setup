from __future__ import annotations
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import sys
import textwrap
import traceback

from support import Adb, SetupError
from session import BillingSession, prepare_server
from sponsor import credit, visit

APP = Path(__file__).resolve().parent
VERSION = '2.0.14'


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


def price_value(row):
    """Numeric price of the first phase; free trials sort first."""
    first = (row.get('phases') or [{}])[0]
    text = str(first.get('price', '')).strip()
    if not text or text.lower().startswith(('free', 'бесплатн')):
        return 0.0
    match = re.search(r'(\d+(?:[.,]\d+)?)', text)
    return float(match.group(1).replace(',', '.')) if match else math.inf


def months_value(row):
    first = (row.get('phases') or [{}])[0]
    match = re.fullmatch(r'P(\d+)([YMWD])', str(first.get('period', '')))
    if not match:
        return 0.0
    days = int(match.group(1)) * {'Y': 365.0, 'M': 30.44, 'W': 7.0, 'D': 1.0}[match.group(2)]
    cycles = int(first.get('cycles') or 1)
    return days * cycles / 30.44


def renewal(row):
    phases = row.get('phases') or []
    return phases[1] if len(phases) > 1 else {}


def renewal_price(row):
    return price_value({'phases': [renewal(row)]})


def short_period(text):
    return {'P1M': 'мес.', 'P1Y': 'год', 'P52W1D': 'год'}.get(text, period(text))


def price_cell(row):
    first = (row.get('phases') or [{}])[0]
    text = str(first.get('price', '—'))
    if str(first.get('period', '')) == 'P1M':
        return text + '/мес.'
    if str(first.get('period', '')) in ('P1Y', 'P52W1D'):
        return text + '/год'
    return text


def time_cell(row):
    months = months_value(row)
    if not months:
        return period(str((row.get('phases') or [{}])[0].get('period', '—')))
    if abs(months - round(months)) < 0.05:
        return str(int(round(months))) + ' мес.'
    return f'{months:.1f} мес.'


def renew_cell(row):
    nxt = renewal(row)
    if not nxt or not nxt.get('price'):
        return '—'
    return str(nxt.get('price', '')) + '/мес.'


def group_offers(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row['title'] or row['product'], []).append(row)
    for items in groups.values():
        items.sort(key=lambda row: (price_value(row), -months_value(row), row['key']))
    return groups


def launch_checkout(session, row):
    """Start the Google Play payment sheet, refreshing the offer token if needed."""
    try:
        fresh = select_current(session, row)
    except Exception as error:
        print('Не удалось обновить вариант (' + str(error) + '), пробую с сохранённым ключом.', flush=True)
        fresh = row
    try:
        return session.checkout(fresh['key'])
    except Exception as error:
        print('Окно оплаты не открылось: ' + str(error) + '. Обновляю вариант и повторяю.', flush=True)
        fresh = select_current(session, row)
        return session.checkout(fresh['key'])


def open_offer(session, row):
    print('\nВыбран вариант: ' + row['title'] + ' — ' + price_cell(row) + ', ' + time_cell(row))
    print(row['product'])
    print(describe(row))
    print('Окончательные условия и подтверждение подписки — в окне Google Play в эмуляторе.')
    answer = input('Открыть окно оплаты в эмуляторе? [Y/n]: ').strip().lower()
    if answer in ('n', 'no', 'н', 'нет'):
        return
    print('Открываю окно оплаты…', flush=True)
    try:
        result = launch_checkout(session, row)
    except Exception as error:
        print('Не удалось открыть окно оплаты: ' + str(error), flush=True)
        result = {'code': -1, 'message': str(error)}
    if result.get('code') == 0:
        print('Окно оплаты открыто в эмуляторе. Переключитесь в окно LDPlayer. Скрипт подписку не подтверждает.', flush=True)
        credit()
        visit()
    else:
        url = session.open_store_page(row['product'])
        print('Google Play не открыл окно: код ' + str(result.get('code')) + ' ' + str(result.get('message', '')), flush=True)
        print('Открыл страницу подписки в Play внутри эмулятора: ' + str(url), flush=True)
    input('Enter — вернуться к таблице: ')


def interactive(session, rows, rescan):
    search = ''
    while True:
        offers = [row for row in rows if row['offer']] or rows
        grouped = group_offers(offers)
        titles = sorted(grouped, key=lambda title: (renewal_price(grouped[title][0]), title))
        if search:
            titles = [title for title in titles if search in title.lower() or any(
                search in (row['product'] + ' ' + str(row['offer'])).lower() for row in grouped[title])]
        table = [(title, row) for title in titles for row in grouped[title]]
        cells = [(title, price_cell(row), time_cell(row), renew_cell(row)) for title, row in table]
        widths = [max(len(item[column]) for item in cells) for column in range(4)]
        widths[0] = max(widths[0], len('Подписка'))
        print(f'\nG1 ACQ Plus — {len(table)} вариантов из Google Play')
        summary = ' · '.join(f"{title} — {len(grouped[title])}" for title in titles)
        for line in textwrap.wrap(summary, 110):
            print(line)
        print()
        print(f"{'№':>3}  {'Подписка':<{widths[0]}}  {'Цена':>{widths[1]}}  {'Срок':>{widths[2]}}  {'Далее':>{widths[3]}}")
        for number, item in enumerate(cells, 1):
            print(f'{number:3}  {item[0]:<{widths[0]}}  {item[1]:>{widths[1]}}  {item[2]:>{widths[2]}}  {item[3]:>{widths[3]}}')
        print('\nНомер — условия и окно оформления; /текст — поиск; r — обновить; q — выход')
        raw = input('> ').strip()
        if raw.lower() == 'q':
            return
        if raw.lower() == 'r':
            rows, search = rescan(), ''
        elif raw.startswith('/'):
            search = raw[1:].lower()
        elif raw.isdigit() and 1 <= int(raw) <= len(table):
            open_offer(session, table[int(raw) - 1][1])


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
    if not any(row['offer'] for row in rows):
        print('Для этого аккаунта Google Play вернул только базовые тарифы.', flush=True)
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
