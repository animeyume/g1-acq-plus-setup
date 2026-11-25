# Starts G1 ACQ Plus from the latest GitHub release.
# Safe for: irm https://github.com/animeyume/g1-acq-plus-setup/releases/latest/download/Start-G1.ps1 | iex
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
$target = Join-Path $env:TEMP 'Windows-OneClick.ps1'
Invoke-WebRequest -UseBasicParsing 'https://github.com/animeyume/g1-acq-plus-setup/releases/latest/download/Windows-OneClick.ps1' -OutFile $target
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $target
