import importlib.metadata
import json
import platform
import sys

def main():
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError('Ожидался Python 3.13; найден ' + sys.version)
    packages = {name: importlib.metadata.version(name) for name in ('frida', 'frida-tools', 'certifi')}
    if packages['frida'] != '17.27.4' or packages['frida-tools'] != '14.13.2':
        raise RuntimeError('Версии зависимостей отличаются от установочного пакета.')
    import frida, frida_tools, ssl, lzma, ctypes
    print(json.dumps({'executable': sys.executable, 'python': platform.python_version(), 'packages': packages}))

if __name__ == '__main__':
    main()
