#requires -Version 5.1
[CmdletBinding()]
param([string]$InstallDir, [switch]$Doctor, [switch]$RuntimeOnly, [string]$Manager, [int]$Instance = -1,
      [ValidateSet('ldplayer', 'mumu')][string]$Emulator = 'ldplayer')
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-CheckedNative {
    param([Parameter(Mandatory=$true)][string]$Program, [string[]]$NativeArgs, [switch]$Capture)
    if (-not (Test-Path -LiteralPath $Program -PathType Leaf)) {
        throw "Исполняемый файл не найден: $Program. Запуск отменён; проверьте загрузку или карантин защиты Windows."
    }
    if ($Capture) {
        $result = @(& $Program @NativeArgs)
        if ($LASTEXITCODE -ne 0) { throw "Команда завершилась с кодом $LASTEXITCODE : $Program" }
        return (($result -join "`n").Trim())
    }
    & $Program @NativeArgs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Команда завершилась с кодом $LASTEXITCODE : $Program" }
}

function Initialize-G1Runtime {
    param([string]$Root, [string]$Uv)
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Root 'managed-python'
    $env:UV_CACHE_DIR = Join-Path $Root 'cache'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    # No MSI/EXE Python installer, no PATH changes and no Windows registry registration.
    Invoke-CheckedNative -Program $Uv -NativeArgs @('--no-config', 'python', 'install', '--no-bin', '--no-registry', '3.13')
    $basePython = Invoke-CheckedNative -Program $Uv -NativeArgs @('--no-config', 'python', 'find', '--no-project', '--managed-python', '--no-python-downloads', '3.13') -Capture
    if (-not $basePython -or -not (Test-Path -LiteralPath $basePython -PathType Leaf)) {
        throw "uv не вернул существующий Python: $basePython. Старый путь python313\python.exe не используется."
    }
    $environmentDir = Join-Path $Root 'venv'
    $python = Join-Path $environmentDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Invoke-CheckedNative -Program $Uv -NativeArgs @('--no-config', 'venv', '--python', $basePython, '--no-python-downloads', $environmentDir)
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Виртуальное окружение не создало python.exe: $python. Основная программа не запущена."
    }
    Invoke-CheckedNative -Program $Uv -NativeArgs @('--no-config', 'pip', 'install', '--python', $python, '--require-hashes', '--only-binary', ':all:', '-r', (Join-Path $Root 'app\requirements.lock'))
    Invoke-CheckedNative -Program $Uv -NativeArgs @('--no-config', 'pip', 'check', '--python', $python)
    Invoke-CheckedNative -Program $python -NativeArgs @('-X', 'utf8', (Join-Path $Root 'app\runtime_check.py'))
    return $python
}

function Install-G1Plus {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem -or
        $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        throw 'Нужна Windows 10/11 x64 на Intel/AMD. Для Mac используйте отдельную команду.'
    }
    if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'G1AcqPlus' }
    $root = [IO.Path]::GetFullPath($InstallDir)
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    foreach ($name in @('', 'app', 'install', 'tools', 'downloads')) {
        $null = New-Item -ItemType Directory -Path (Join-Path $root $name) -Force
    }
    $bundle = Split-Path -Parent $PSScriptRoot
    $sourceApp = Join-Path $bundle 'app'
    if ([IO.Path]::GetFullPath($sourceApp) -ne [IO.Path]::GetFullPath((Join-Path $root 'app'))) {
        Get-ChildItem -LiteralPath $sourceApp -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $root 'app') -Force
        }
        Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $root 'install\windows.ps1') -Force
    }
    Write-Host '[1/3] Подготовка uv и отдельного Python 3.13' -ForegroundColor Cyan
    $uv = Join-Path $root 'tools\uv.exe'
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        $archive = Join-Path $root 'downloads\uv-0.13.10-windows.zip'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/download/0.13.10/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '297b3881fe1adfb922becc2c1613202abb41dc83ea5ff43d472aa087ca1f8683') {
            throw 'Контрольная сумма uv не совпала. Ничего не запускается.'
        }
        $stage = Join-Path $root ('downloads\uv-' + [Guid]::NewGuid().ToString('N'))
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $stage)
        try {
            $binary = @(Get-ChildItem -LiteralPath $stage -Filter uv.exe -Recurse -File)
            if ($binary.Count -ne 1) { throw 'В архиве uv не найден единственный uv.exe.' }
            Copy-Item -LiteralPath $binary[0].FullName -Destination $uv
        }
        finally { Remove-Item -LiteralPath $stage -Recurse -Force }
    }
    $uvVersion = Invoke-CheckedNative -Program $uv -NativeArgs @('--version') -Capture
    if ($uvVersion -notmatch '^uv 0\.13\.10(?:\s|$)') { throw "Неожиданная версия uv: $uvVersion" }
    # Runtime setup writes progress to the console, so capture only the final executable via a script variable.
    $runtimeOutput = @(Initialize-G1Runtime -Root $root -Uv $uv)
    $python = [string]$runtimeOutput[-1]
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Проверенный путь Python потерян: $python" }
    Write-Host '[2/3] Создание повторного запуска' -ForegroundColor Cyan
    $launcher = Join-Path $root 'Start-G1.cmd'
    $body = "@echo off`r`nsetlocal`r`nchcp 65001 >nul`r`npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File `"%~dp0install\windows.ps1`" -InstallDir `"%~dp0.`" %*`r`nset RESULT=%ERRORLEVEL%`r`nif not %RESULT%==0 pause`r`nexit /b %RESULT%`r`n"
    [IO.File]::WriteAllText($launcher, $body, [Text.Encoding]::ASCII)
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'G1 ACQ Plus.lnk'))
        $shortcut.TargetPath = $launcher
        $shortcut.WorkingDirectory = $root
        $shortcut.Save()
    }
    catch { Write-Host "Ярлык не создан. Повторный запуск: $launcher" }
    if ($RuntimeOnly) { Write-Host "Runtime готов: $python"; return }
    Write-Host "[3/3] $Emulator, Android root и каталог Google Play" -ForegroundColor Cyan
    try { $env:COLUMNS = [string][Console]::WindowWidth; $env:LINES = [string][Console]::WindowHeight } catch {}
    $appArgs = @('-X', 'utf8', (Join-Path $root 'app\g1plus.py'), '--work', $root, '--emulator', $Emulator)
    if ($Doctor) { $appArgs += '--doctor' }
    if ($Manager) { $appArgs += @('--manager', $Manager) }
    if ($Instance -ge 0) { $appArgs += @('--instance', [string]$Instance) }
    Invoke-CheckedNative -Program $python -NativeArgs $appArgs
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Install-G1Plus }
    catch { Write-Host ("ОШИБКА УСТАНОВКИ: " + $_.Exception.Message) -ForegroundColor Red; exit 1 }
}
