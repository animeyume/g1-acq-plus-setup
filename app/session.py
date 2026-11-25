from pathlib import Path
import lzma
import shutil
import struct
import threading
import time

from support import SetupError, download


def prepare_server(adb, work, version):
    remote = '/data/local/tmp/frida-server'
    running = adb.shell('pidof', 'frida-server', check=False)
    current = adb.shell(remote, '--version', check=False)
    if running and current.strip() == version:
        return
    if running:
        answer = input('В этом Android работает другая версия Frida. Перезапустить её? [y/N]: ').strip().lower()
        if answer not in ('y', 'yes', 'д', 'да'):
            raise SetupError('Frida оставлена без изменений.')
    abi = adb.shell('getprop', 'ro.product.cpu.abi')
    tag = {'arm64-v8a': ('android-arm64', 183), 'x86_64': ('android-x86_64', 62)}.get(abi)
    if not tag:
        raise SetupError('Неподдерживаемая архитектура Android: ' + abi)
    cache = Path(work) / 'assets'
    cache.mkdir(exist_ok=True)
    name = f'frida-server-{version}-{tag[0]}'
    binary = cache / name
    if not binary.is_file():
        print('Загружаю frida-server для ' + abi + '…', flush=True)
        packed = download(f'https://github.com/frida/frida/releases/download/{version}/{name}.xz', cache / (name + '.xz'))
        partial = cache / (name + '.part')
        try:
            with lzma.open(packed, 'rb') as source, partial.open('wb') as target:
                shutil.copyfileobj(source, target)
            partial.replace(binary)
        finally:
            packed.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)
    head = binary.read_bytes()[:20]
    if head[:4] != b'\x7fELF' or struct.unpack_from('<H', head, 18)[0] != tag[1]:
        raise SetupError('Архитектура frida-server не совпала с Android.')
    if running:
        adb.shell('su', '-c', 'killall frida-server', check=False)
    adb.command('push', binary, remote)
    adb.shell('chmod', '755', remote)
    adb.shell('su', '-c', remote + ' -D')
    for _ in range(20):
        if adb.shell('pidof', 'frida-server', check=False):
            return
        time.sleep(0.5)
    raise SetupError('frida-server не запустился. Проверьте разрешение root в эмуляторе.')


class BillingSession:
    def __init__(self, adb, app_dir):
        self.adb, self.app_dir = adb, Path(app_dir)
        self.session = self.script = None
        self.wait_ready = threading.Event()
        self.result = None

    def message(self, message, data):
        if message.get('type') == 'error':
            self.result = {'code': -1, 'message': message.get('stack', message.get('description', 'Frida error'))}
            self.wait_ready.set()
        elif message.get('type') == 'send':
            payload = message.get('payload') or {}
            if payload.get('event') == 'ready':
                self.result = payload
                self.wait_ready.set()
            elif payload.get('event') == 'purchase_result':
                print('\nGoogle Play: результат ' + str(payload.get('code')) + ' ' + payload.get('message', ''))

    def attach(self, pid):
        import frida
        import frida_tools
        module = self.app_dir / 'billing-client.apk'
        self.adb.shell('chmod', '644', '/data/local/tmp/g1plus-billing.apk', check=False)
        self.adb.command('push', module, '/data/local/tmp/g1plus-billing.apk')
        self.adb.shell('chmod', '444', '/data/local/tmp/g1plus-billing.apk')
        device = frida.get_device_manager().get_device(self.adb.serial, timeout=15)
        self.session = device.attach(pid)
        bridge = Path(frida_tools.__file__).parent / 'bridges/java.js'
        if not bridge.is_file():
            raise SetupError('В frida-tools отсутствует Java bridge. Повторите установку зависимостей.')
        source = bridge.read_text(encoding='utf-8') + '\n;var Java = bridge;\n' + (self.app_dir / 'billing.js').read_text(encoding='utf-8')
        self.script = self.session.create_script(source)
        self.script.on('message', self.message)
        self.script.load()
        if not self.wait_ready.wait(45):
            raise SetupError('Google Play не ответил за 45 секунд. Проверьте вход в аккаунт и сеть эмулятора.')
        if not self.result or self.result.get('code') != 0:
            raise SetupError('BillingClient: ' + str(self.result))

    def query(self, ids):
        result = self.script.exports_sync.query(ids)
        if result.get('code') != 0:
            raise SetupError('Google Play: ' + str(result.get('code')) + ' ' + result.get('message', ''))
        return result['offers']

    def checkout(self, key):
        return self.script.exports_sync.open_checkout(key)

    def open_store_page(self, product):
        """Fallback when the billing flow cannot start in the hooked process."""
        url = ('https://play.google.com/store/account/subscriptions?sku=' + str(product)
               + '&package=' + 'com.google.android.apps.subscriptions.red')
        self.adb.shell('am', 'start', '-a', 'android.intent.action.VIEW', '-d', url, check=False)
        return url

    def close(self):
        for target, method in ((self.script, 'unload'), (self.session, 'detach')):
            if target:
                try:
                    getattr(target, method)()
                except Exception:
                    pass
