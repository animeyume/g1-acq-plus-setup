"""Visible sponsor credit, followed by a single normal browser navigation."""
import shutil
import sys
import webbrowser

URL = 'https://animeyume.online'
NOTICE = 'СКРИПТ СПОНСИРОВАН САЙТОМ'

FONT = {
    'A': ('01110', '10001', '11111', '10001', '10001'),
    'N': ('10001', '11001', '10101', '10011', '10001'),
    'I': ('11111', '00100', '00100', '00100', '11111'),
    'M': ('10001', '11011', '10101', '10001', '10001'),
    'E': ('11111', '10000', '11110', '10000', '11111'),
    'Y': ('10001', '01010', '00100', '00100', '00100'),
    'U': ('10001', '10001', '10001', '10001', '01110'),
    '.': ('00000', '00000', '00000', '00110', '00110'),
    'O': ('01110', '10001', '10001', '10001', '01110'),
    'L': ('10000', '10000', '10000', '10000', '11111'),
}


def banner(columns, lines):
    width = max(28, min(columns - 1, 240))
    height = max(16, min(lines - 1, 80))
    words = ['ANIMEYUME.ONLINE'] if width >= 99 else ['ANIMEYUME', '.ONLINE']
    if width < 57:
        words = ['ANIME', 'YUME', '.ONLINE']
    pixel_width = max(len(word) * 6 - 1 for word in words)
    if pixel_width > width - 4:
        art = ['animeyume.online']
    else:
        scale_x = max(1, min(3, (width - 4) // pixel_width))
        # A terminal cell is already roughly twice as tall as it is wide.
        # Stretching rows independently makes letters unreadably thin/tall.
        scale_y = max(1, (scale_x + 1) // 2)
        art = []
        for index, word in enumerate(words):
            if index:
                art.append('')
            for row in range(5):
                pixels = '0'.join(FONT[char][row] for char in word)
                # Retain trailing pixels so centering never shifts glyph rows.
                rendered = ''.join(('█' if bit == '1' else ' ') * scale_x for bit in pixels)
                art.extend([rendered] * scale_y)
    content = [NOTICE, '', *art, '', 'animeyume.online', 'Сейчас сайт спонсора откроется в браузере.']
    # Keep text intact on narrow windows instead of clipping the disclosure.
    wrapped = []
    for line in content:
        if len(line) > width - 4:
            import textwrap
            wrapped.extend(textwrap.wrap(line, width - 4))
        else:
            wrapped.append(line)
    padding = max(0, height - len(wrapped) - 2)
    body = [''] * (padding // 2) + wrapped + [''] * (padding - padding // 2)
    border = '═' * width
    return '\n'.join([border, *(line.center(width) for line in body), border])


def announce():
    size = shutil.get_terminal_size((100, 30))
    if sys.stdout.isatty():
        print('\033[2J\033[H', end='')
    print(banner(size.columns, size.lines), flush=True)
    try:
        opened = webbrowser.open(URL, new=2)
    except Exception:
        opened = False
    if not opened:
        print('Браузер не открылся. Сайт спонсора: ' + URL, flush=True)
    return opened
