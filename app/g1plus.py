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
from sponsor import credit, visit

APP = Path(__file__).resolve().parent
VERSION = '2.1.1'

# Название тарифа так, как его видит пользователь.
PLAN_NAMES = {
    '30 GB': '30 ГБ',
    '100 GB': '100 ГБ',
    '200 GB': '200 ГБ',
    'Google AI Plus (400 GB)': 'AI Plus 400 ГБ',
    'Google AI Plus (2 TB)': 'AI Plus 2 ТБ',
    'Google AI Pro (5 TB)': 'AI Pro 5 ТБ',
    'Google AI Pro (10 TB)': 'AI Pro 10 ТБ',
    'Google AI Ultra (20 TB)': 'AI Ultra 20 ТБ',
    'Google AI Ultra (30 TB)': 'AI Ultra 30 ТБ',
    'YouTube Premium Individual Add On': 'YouTube Premium',
}

# Ultra стоит первым, чтобы его было видно сразу.
FAMILY_ORDER = ['AI Ultra 30 ТБ', 'AI Ultra 20 ТБ', 'AI Pro 10 ТБ', 'AI Pro 5 ТБ',
                'AI Plus 2 ТБ', 'AI Plus 400 ГБ', '200 ГБ', '100 ГБ', '30 ГБ', 'YouTube Premium']

ULTRA_FAMILIES = ('AI Ultra 30 ТБ', 'AI Ultra 20 ТБ')

TITLE_RULES = [
    (r'AI\s+Ultra.*30', 'AI Ultra 30 ТБ'),
    (r'AI\s+Ultra.*20', 'AI Ultra 20 ТБ'),
    (r'AI\s+Pro.*10', 'AI Pro 10 ТБ'),
    (r'AI\s+Pro.*5', 'AI Pro 5 ТБ'),
    (r'AI\s+Plus.*400', 'AI Plus 400 ГБ'),
    (r'AI\s+Plus.*2', 'AI Plus 2 ТБ'),
    (r'\b200\s*GB', '200 ГБ'),
    (r'\b100\s*GB', '100 ГБ'),
    (r'\b30\s*GB', '30 ГБ'),
]

ID_RULES = [
    (r'^g1\.(?:ai\.ultra|ai\.pro|play\.ai\.pro)', 'AI Ultra 30 ТБ'),
    (r'^g1\.(?:20tb|play\.20tb)', 'AI Ultra 20 ТБ'),
    (r'^g1\.10tb', 'AI Pro 10 ТБ'),
    (r'^g1\.(?:5tb|2tb\.ai|play\.2tb\.ai)', 'AI Pro 5 ТБ'),
    (r'^g1\.(?:2tb|play\.2tb)(?!\.ai)', 'AI Plus 2 ТБ'),
    (r'^g1\.(?:helium|play\.helium)', 'AI Plus 400 ГБ'),
    (r'^g1\.200gb', '200 ГБ'),
    (r'^g1\.100gb', '100 ГБ'),
    (r'^g1\.30gb', '30 ГБ'),
    (r'youtube', 'YouTube Premium'),
]

KINDS = {'базовый': 'без акции и пробного периода',
         'пробный': 'бесплатный первый период',
         'акция': 'скидка на первый период'}


def load_products(path=APP / 'products.json'):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    products = data['products']
    if not products or any(not re.fullmatch(r'g1\.[A-Za-z0-9._-]{1,150}', row['id']) for row in products):
        raise SetupError('Некорректный каталог товаров.')
    return data


def catalog_labels(catalog):
    labels = {}
    for row in catalog['products']:
        label = str(row.get('label') or '').strip()
        if label and label != row['id']:
            labels[row['id']] = label
    return labels


def plan_for(product, label, title):
    """Русское название тарифа: сначала метка каталога, затем ответ Play, затем SKU."""
    for source in (label, title):
        if source and source in PLAN_NAMES:
            return PLAN_NAMES[source]
    text = re.sub(r'\s*\(Google One\)\s*$', '', str(title or '')).strip()
    for pattern, name in TITLE_RULES:
        if re.search(pattern, text, re.I):
            return name
    for pattern, name in ID_RULES:
        if re.search(pattern, product, re.I):
            return name
    return text or product


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
    if len(phases) > 1:
        return phases[1]
    if phases and phases[0].get('recurrence') == 1:
        return phases[0]
    return {}


def renewal_price(row):
    return price_value({'phases': [renewal(row)]})


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
    text = str(nxt.get('price', ''))
    if str(nxt.get('period', '')) == 'P1M':
        return text + '/мес.'
    if str(nxt.get('period', '')) in ('P1Y', 'P52W1D'):
        return text + '/год'
    return text


def offer_type(row):
    if not row.get('offer'):
        return 'базовый'
    return 'пробный' if price_value(row) <= 0.0 else 'акция'


def label_rows(rows, labels):
    for row in rows:
        row['plan'] = plan_for(row['product'], labels.get(row['product'], ''), row.get('title', ''))
        row['kind'] = offer_type(row)
    return rows


def group_offers(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row['plan'], []).append(row)
    for items in groups.values():
        items.sort(key=lambda row: (price_value(row), -months_value(row), row['key']))
    return groups


def family_rank(name):
    return FAMILY_ORDER.index(name) if name in FAMILY_ORDER else len(FAMILY_ORDER)


def normalized(text):
    text = str(text).lower().replace('ё', 'е')
    text = re.sub(r'(\d+)\s*(?:tb|тб)', r'\1тб', text)
    text = re.sub(r'(\d+)\s*(?:gb|гб)', r'\1гб', text)
    return re.sub(r'\s+', ' ', text)


def matches(query, row):
    haystack = normalized(' '.join([row['plan'], row['product'], str(row.get('offer') or ''),
                                    str(row.get('base_plan') or ''), str(row.get('title') or '')]))
    return all(token in haystack for token in normalized(query).split())


def scan(session, catalog, limit=None):
    products = catalog['products'][:limit] if limit else catalog['products']
    found = {}
    batches = [products[index:index + 10] for index in range(0, len(products), 10)]
    for number, batch in enumerate(batches, 1):
        print(f'Google Play: пакет {number}/{len(batches)}…', flush=True)
        try:
            rows = session.query([item['id'] for item in batch])
        except SetupError as error:
            print('  Пакет отклонён (' + str(error) + '), проверяю позиции по одной…', flush=True)
            rows = []
            for item in batch:
                try:
                    rows.extend(session.query([item['id']]))
                except SetupError:
                    continue
        for row in rows:
            found[row['key']] = row
    return sorted(found.values(), key=lambda row: (row['title'], row['product'], row['base_plan'], row['offer']))


IOS_ULTRA_NOTE = (
    'В README проекта «1 месяц Ultra за 0 $» — это SKU App Store для iOS: '
    'g1.20tb.1month_eft.acq (20 ТБ) и g1.ai.ultra.1month_eft.acq (30 ТБ). '
    'Модуль Shadowrocket подменяет ими ответ one.google.com в iOS-приложении Google, '
    'поэтому кассир App Store показывает месяц бесплатно, а запасной вариант «90p» — '
    'три месяца за 9,99 $ / 19,99 $. В Google Play этих товаров нет: там Ultra продаётся '
    'только базовым тарифом.')


def ultra_ids(catalog):
    """Все Ultra-идентификаторы: из каталога и дополнительный список для проверки."""
    ids = []
    for row in catalog['products']:
        if plan_for(row['id'], str(row.get('label') or ''), '') in ULTRA_FAMILIES:
            ids.append(row['id'])
    for item in catalog.get('ultra_candidates') or []:
        if isinstance(item, str) and item not in ids:
            ids.append(item)
    return ids


def check_ultra(session, catalog, labels):
    """Проверяет каждый Ultra-SKU отдельно и показывает, что Play отдаёт, а что нет."""
    known = {row['id']: bool(row.get('catalog_seen', True)) for row in catalog['products']}
    ids = ultra_ids(catalog)
    print('Проверка Ultra: ' + str(len(ids)) + ' SKU, по одному запросу к Google Play…', flush=True)
    found, missing = [], []
    for index, product in enumerate(ids, 1):
        try:
            rows = session.query([product])
        except SetupError as error:
            rows, reason = [], str(error)[:90]
        else:
            reason = 'нет предложений в ответе'
        if rows:
            found.extend(rows)
        else:
            missing.append((product, reason))
        if index % 10 == 0 and index < len(ids):
            print('  ' + str(index) + '/' + str(len(ids)) + '…', flush=True)
    found = label_rows(found, labels)
    print()
    print('Google Play отдал по Ultra:')
    if found:
        for row in sorted(found, key=lambda item: (family_rank(item['plan']), price_value(item))):
            print('  ' + row['product'] + ' — ' + row['plan'] + ', ' + price_cell(row) + ', ' + time_cell(row)
                  + ', далее ' + renew_cell(row) + ', оффер: ' + (row.get('offer') or 'нет (базовый тариф)'))
    else:
        print('  ничего.')
    if missing:
        print()
        print('Google Play не отдал (' + str(len(missing)) + ' SKU) — нет такого товара или нет доступа у аккаунта:')
        for product, reason in missing:
            note = '' if known.get(product, True) else ' · нет в Android-каталоге проекта'
            print('  ' + product + note + ' — ' + reason)
    promos = [row for row in found if row.get('offer')]
    print()
    if promos:
        print('Вывод: у аккаунта есть акционные Ultra-предложения — они добавлены в таблицу.')
    else:
        print('Вывод: бесплатного периода и скидок по Ultra Google Play не даёт.')
    for line in wrap_items([IOS_ULTRA_NOTE], width=110):
        print(line)
    return found


def select_current(session, selected):
    rows = session.query([selected['product']])
    for row in rows:
        if row['key'] == selected['key']:
            return row
    raise SetupError('Google Play больше не возвращает это предложение. Обновите список клавишей r.')


def launch_checkout(session, row):
    """Открывает окно оплаты Google Play; при необходимости обновляет токен и активное окно."""
    try:
        fresh = select_current(session, row)
    except Exception as error:
        print('Не удалось обновить вариант (' + str(error) + '), пробую с сохранённым ключом.', flush=True)
        fresh = row
    result = session.checkout(fresh['key'])
    if result.get('code') == 0:
        return result
    if result.get('code') == -2:
        print('Google One не в активном окне. Поднимаю его и повторяю.', flush=True)
        session.foreground(delay=1.6)
        result = session.checkout(fresh['key'])
    return result


def open_offer(session, row):
    print()
    print('Выбран вариант: ' + row['plan'] + ' — ' + price_cell(row) + ', ' + time_cell(row))
    print('SKU: ' + row['product'] + ' · база: ' + (row.get('base_plan') or '—')
          + ' · оффер: ' + (row.get('offer') or 'нет (базовый тариф)'))
    print('Условия: ' + describe(row))
    print('Окончательные условия и подтверждение подписки — в окне Google Play в эмуляторе.')
    print('Поднимаю Google One и открываю окно оплаты…', flush=True)
    try:
        result = launch_checkout(session, row)
    except Exception as error:
        print('Не удалось открыть окно оплаты: ' + str(error), flush=True)
        result = {'code': -1, 'message': str(error)}
    if result.get('code') == 0:
        where = result.get('activity') or 'окно Google One'
        print('Окно оплаты Google Play открыто в эмуляторе (' + where + ').', flush=True)
        print('Переключитесь в окно LDPlayer и подтвердите подписку вручную — скрипт её не подтверждает.', flush=True)
        credit()
        visit()
    else:
        code = result.get('code')
        print('Окно оплаты Google Play не открылось: код ' + str(code) + ' ' + str(result.get('message', '')), flush=True)
        windows = session.activities()
        if windows:
            print('Окна Google One в процессе: ' + ', '.join(
                item.get('name', '?') + (' (активно)' if not item.get('paused') else '') for item in windows), flush=True)
        else:
            print('В процессе Google One нет ни одного окна. Откройте Google One в эмуляторе и повторите.', flush=True)
        url = session.open_store_page(row['product'])
        print('Открыл страницу этой подписки в Play внутри эмулятора: ' + str(url), flush=True)
        print('Если окно не появилось: в Google One откройте «План» и выберите тариф ' + row['plan'] + ' вручную.', flush=True)
    input('Enter — вернуться к таблице: ')


def render_table(rows):
    plan_w = max([len('Подписка')] + [len(row['plan']) for row in rows])
    kind_w = max([len('Тип')] + [len(row['kind']) for row in rows])
    price_w = max([len('Цена')] + [len(price_cell(row)) for row in rows])
    time_w = max([len('Срок')] + [len(time_cell(row)) for row in rows])
    renew_w = max([len('Далее')] + [len(renew_cell(row)) for row in rows])
    print(f"{'№':>3}  {'Подписка':<{plan_w}}  {'Тип':<{kind_w}}  {'Цена':>{price_w}}  {'Срок':>{time_w}}  {'Далее':>{renew_w}}")
    for number, row in enumerate(rows, 1):
        print(f"{number:>3}  {row['plan']:<{plan_w}}  {row['kind']:<{kind_w}}  "
              f"{price_cell(row):>{price_w}}  {time_cell(row):>{time_w}}  {renew_cell(row):>{renew_w}}")


def wrap_items(items, width=110, separator=' · '):
    lines, current = [], ''
    for item in items:
        candidate = item if not current else current + separator + item
        if current and len(candidate) > width:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render(rows, search, show_base):
    visible = [row for row in rows if show_base or row['kind'] != 'базовый']
    if search:
        visible = [row for row in visible if matches(search, row)]
    grouped = group_offers(visible)
    names = sorted(grouped, key=lambda name: (family_rank(name), renewal_price(grouped[name][0]), name))
    table = [row for name in names for row in grouped[name]]
    print(f'\nG1 ACQ Plus {VERSION} — Google Play вернул {len(rows)} вариантов, показано {len(table)}'
          + (f' · фильтр /{search}' if search else ''))
    for line in wrap_items([f'{name} — {len(grouped[name])}' for name in names]) or ['ничего не найдено']:
        print(line)
    print()
    if table:
        render_table(table)
    else:
        print('Под этот фильтр ничего не подошло. Одна «/» сбрасывает поиск.')
    ultra_rows = [row for row in visible if row['plan'] in ULTRA_FAMILIES]
    if ultra_rows and not any(row.get('offer') for row in ultra_rows):
        print()
        print('Ultra в Google Play — только базовый тариф, без пробного периода и скидки. '
              'Что именно отдаёт Play по каждому Ultra-SKU — команда /ultra-check.')
    print()
    print('Тип: базовый — ' + KINDS['базовый'] + '; пробный — ' + KINDS['пробный'] + '; акция — ' + KINDS['акция'] + '.')
    print('Номер — условия и окно оплаты · /текст — поиск · /ultra — только Ultra · '
          '/ultra-check — проверка Ultra в Google Play')
    print('a — показать/скрыть базовые тарифы · r — обновить каталог · q — выход')
    return table


def interactive(session, rows, rescan, catalog, labels):
    search = ''
    show_base = True
    while True:
        table = render(rows, search, show_base)
        raw = input('> ').strip()
        if not raw:
            continue
        command = raw.lower()
        if command == 'q':
            return
        if command == 'r':
            try:
                rows, search = rescan(), ''
            except Exception as error:
                print('Не удалось обновить каталог: ' + str(error), flush=True)
        elif command == 'a':
            show_base = not show_base
        elif command in ('/ultra-check', '/ultra-scan'):
            try:
                extra = check_ultra(session, catalog, labels)
            except Exception as error:
                print('Проверка Ultra не удалась: ' + str(error), flush=True)
                continue
            known = {row['key'] for row in rows}
            added = [row for row in extra if row['key'] not in known]
            if added:
                rows = label_rows(rows + added, labels)
                print('Добавлено в таблицу: ' + str(len(added)) + '.', flush=True)
            else:
                print('Новых Ultra-вариантов в таблицу не добавлено.', flush=True)
        elif raw.startswith('/'):
            search = raw[1:].strip()
        elif raw.isdigit() and 1 <= int(raw) <= len(table):
            open_offer(session, table[int(raw) - 1])
        else:
            print('Не понял команду. Номер, /текст, a, r или q.', flush=True)


def run_catalog(session, catalog, work, limit=None, scan_only=False):
    labels = catalog_labels(catalog)
    print(f"Каталог источника: {catalog['catalog_products']} SKU; всего опубликованных кандидатов: {len(catalog['products'])}.")

    def rescan():
        rows = label_rows(scan(session, catalog, limit), labels)
        path = work / 'live-offers.json'
        path.write_text(json.dumps({'source_commit': catalog['commit'], 'offers': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Google Play вернул {len(rows)} вариантов. Отчёт: {path}', flush=True)
        if not any(row['plan'] in ULTRA_FAMILIES for row in rows):
            print('Ultra в ответе Google Play не найден: аккаунт не получает эти тарифы '
                  '(они доступны не во всех странах и не для всех аккаунтов). '
                  'Команда /ultra-check проверяет каждый Ultra-SKU по отдельности.', flush=True)
        return rows

    rows = rescan()
    if scan_only:
        return
    if not rows:
        raise SetupError('Для этого аккаунта Google Play не вернул предложения из каталога.')
    interactive(session, rows, rescan, catalog, labels)


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
    parser = argparse.ArgumentParser(description='G1 ACQ Plus: живой каталог Google Play')
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
    print(f'G1 ACQ Plus {VERSION} — Android-версия каталога Dgkncn/g1-acq-plus.', flush=True)
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
