"""Build reproducible, self-contained installers from an explicit allowlist."""
import base64
import hashlib
import io
from pathlib import Path
import textwrap
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dist'
VERSION = '2.0.0'
APP_FILES = (
    'billing-client.apk', 'billing.js', 'check-publisher.ps1', 'devices.py',
    'g1plus.py', 'ldplayer.py', 'mumu_mac.py', 'mumu_win.py', 'products.json',
    'requirements.lock', 'runtime_check.py', 'session.py', 'sponsor.py', 'support.py',
)
PAYLOAD = [*(f'app/{name}' for name in APP_FILES), 'install/mac.zsh', 'install/windows.ps1',
           'README-RU.txt', 'SOURCES.txt', 'dependency-versions.json']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def payload():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(PAYLOAD):
            entry = zipfile.ZipInfo(name, date_time=(2026, 11, 25, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = (0o100755 if name.endswith('.zsh') else 0o100644) << 16
            archive.writestr(entry, (ROOT / name).read_bytes())
    return output.getvalue()


WINDOWS = r'''#requires -Version 5.1
[CmdletBinding()]
param([string]$InstallDir, [switch]$Doctor, [switch]$RuntimeOnly, [string]$Manager, [int]$Instance = -1,
      [ValidateSet('ldplayer', 'mumu')][string]$Emulator = 'ldplayer')
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
$ErrorActionPreference = 'Stop'
$g1Stage = Join-Path ([IO.Path]::GetTempPath()) ('G1AcqPlus-' + [Guid]::NewGuid().ToString('N'))
$g1Data = @'
@@PAYLOAD@@
'@
try {
    $g1Bytes = [Convert]::FromBase64String($g1Data)
    $g1Sha = [Security.Cryptography.SHA256]::Create()
    try { $g1Actual = -join ($g1Sha.ComputeHash($g1Bytes) | ForEach-Object { $_.ToString('x2') }) }
    finally { $g1Sha.Dispose() }
    if ($g1Actual -ne '@@SHA256@@') { throw 'Встроенный архив повреждён. Установка остановлена.' }
    $null = New-Item -ItemType Directory -Path $g1Stage
    $g1Zip = Join-Path $g1Stage 'payload.zip'
    [IO.File]::WriteAllBytes($g1Zip, $g1Bytes)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $g1Payload = Join-Path $g1Stage 'payload'
    [IO.Compression.ZipFile]::ExtractToDirectory($g1Zip, $g1Payload)
    & (Join-Path $g1Payload 'install\windows.ps1') @PSBoundParameters
}
catch { Write-Host ('ОШИБКА: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
finally { if (Test-Path -LiteralPath $g1Stage) { Remove-Item -LiteralPath $g1Stage -Recurse -Force } }
'''

MAC = r'''#!/bin/zsh
set -euo pipefail
g1_stage="$(mktemp -d "${TMPDIR:-/tmp}/G1AcqPlus.XXXXXX")"
trap 'rm -rf -- "$g1_stage"' EXIT
cat > "$g1_stage/payload.b64" <<'G1_PAYLOAD_BASE64'
@@PAYLOAD@@
G1_PAYLOAD_BASE64
/usr/bin/base64 -D -i "$g1_stage/payload.b64" -o "$g1_stage/payload.zip"
[[ "$(/usr/bin/shasum -a 256 "$g1_stage/payload.zip" | /usr/bin/awk '{print $1}')" == '@@SHA256@@' ]] || { print -u2 'Встроенный архив повреждён. Установка остановлена.'; exit 1; }
/usr/bin/ditto -xk "$g1_stage/payload.zip" "$g1_stage/payload"
/bin/zsh "$g1_stage/payload/install/mac.zsh" "$@"
'''


def main():
    OUT.mkdir(exist_ok=True)
    data = payload()
    encoded = '\n'.join(textwrap.wrap(base64.b64encode(data).decode('ascii'), 120))
    (OUT / f'G1AcqPlus-{VERSION}.zip').write_bytes(data)
    for name, template in (('Windows-OneClick.ps1', WINDOWS), ('Mac-OneClick.command', MAC)):
        result = template.replace('@@PAYLOAD@@', encoded).replace('@@SHA256@@', digest(data))
        (OUT / name).write_text(result, encoding='utf-8-sig' if name.endswith('.ps1') else 'utf-8', newline='\n')
        if name.endswith('.command'):
            (OUT / name).chmod(0o755)
    names = [f'G1AcqPlus-{VERSION}.zip', 'Windows-OneClick.ps1', 'Mac-OneClick.command']
    (OUT / 'SHA256SUMS.txt').write_text(''.join(digest((OUT / name).read_bytes()) + '  ' + name + '\n' for name in names), encoding='ascii')
    print(f'Built {len(PAYLOAD)} payload files; ZIP {len(data)} bytes; SHA256 {digest(data)}')


if __name__ == '__main__':
    main()
