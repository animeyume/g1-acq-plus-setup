"""Generate pinned download commands that stop cleanly on download errors."""
import argparse
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def windows_command(url, checksum):
    return (
        "& { $ErrorActionPreference='Stop'; $g1=Join-Path $env:TEMP ('G1-'+[guid]::NewGuid()+'.ps1'); "
        "try { [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072; "
        "Invoke-WebRequest -UseBasicParsing '" + url + "' -OutFile $g1 -ErrorAction Stop; "
        "if(-not (Test-Path -LiteralPath $g1 -PathType Leaf)){throw 'Download did not create a file'}; "
        "if((Get-FileHash -LiteralPath $g1 -Algorithm SHA256 -ErrorAction Stop).Hash -ne '" + checksum + "'){throw 'Checksum mismatch'}; "
        "& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $g1; "
        "if($LASTEXITCODE -ne 0){throw ('Installer exited with code '+$LASTEXITCODE)} "
        "} finally { if(Test-Path -LiteralPath $g1 -PathType Leaf){Remove-Item -LiteralPath $g1 -Force -ErrorAction SilentlyContinue} } }"
    )


def windows_portable_command(url, checksum):
    # No variable interpolation or shell operators in the surrounding shell:
    # both cmd.exe and PowerShell pass this literal argument to powershell.exe.
    path = "([IO.Path]::Combine([IO.Path]::GetTempPath(),('G1-'+[Diagnostics.Process]::GetCurrentProcess().Id+'.ps1')))"
    body = (
        "try { [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072; "
        "Invoke-WebRequest -UseBasicParsing '" + url + "' -OutFile " + path + " -ErrorAction Stop; "
        "if(-not (Test-Path -LiteralPath " + path + " -PathType Leaf)){throw 'Download did not create a file'}; "
        "if((Get-FileHash -LiteralPath " + path + " -Algorithm SHA256 -ErrorAction Stop).Hash -ne '" + checksum + "'){throw 'Checksum mismatch'}; "
        ". " + path + "; exit (Get-Variable -Name LASTEXITCODE -ValueOnly -ErrorAction SilentlyContinue) "
        "} finally { if(Test-Path -LiteralPath " + path + " -PathType Leaf){Remove-Item -LiteralPath " + path + " -Force -ErrorAction SilentlyContinue} }"
    )
    if any(char in body for char in ('$', '"', '%', '!', '\n', '\r')):
        raise ValueError('Command contains characters requiring shell-specific escaping')
    result = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "' + body + '"'
    if len(result) >= 8191:
        raise ValueError('Command exceeds the cmd.exe command-line limit')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', default='animeyume/g1-acq-plus-setup')
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        raise SystemExit('Invalid repository')
    if not re.fullmatch(r'[a-f0-9]{40}', args.commit):
        raise SystemExit('Use a complete commit SHA')
    out = ROOT / 'dist'
    url = 'https://raw.githubusercontent.com/' + args.repository + '/' + args.commit + '/'
    win_hash = hashlib.sha256((out / 'Windows-OneClick.ps1').read_bytes()).hexdigest()
    mac_hash = hashlib.sha256((out / 'Mac-OneClick.command').read_bytes()).hexdigest()
    bootstrap = windows_command(url + 'Windows-OneClick.ps1', win_hash)
    windows = windows_portable_command(url + 'Windows-OneClick.ps1', win_hash)
    mac = (
        "(g1_script=\"$(mktemp -t g1setup)\"; trap 'rm -f -- \"$g1_script\"' EXIT; "
        "curl -fL --retry 2 --proto '=https' --proto-redir '=https' '" + url + "Mac-OneClick.command' -o \"$g1_script\" && "
        "[ \"$(shasum -a 256 \"$g1_script\" | awk '{print $1}')\" = '" + mac_hash + "' ] && /bin/zsh \"$g1_script\")"
    )
    content = 'Windows — PowerShell или cmd, одна строка:\n' + windows + '\n\nMac Terminal — одна строка:\n' + mac + '\n'
    (ROOT / 'COMMANDS.txt').write_text(content, encoding='utf-8')
    (out / 'COMMANDS.txt').write_text(content, encoding='utf-8')
    (out / 'windows-command.ps1').write_text(bootstrap + '\n', encoding='utf-8-sig')
    (out / 'windows-portable-command.txt').write_text(windows + '\n', encoding='ascii')
    (out / 'mac-command.zsh').write_text(mac + '\n', encoding='utf-8')
    print('Generated scoped, fail-fast download commands.')


if __name__ == '__main__':
    main()
