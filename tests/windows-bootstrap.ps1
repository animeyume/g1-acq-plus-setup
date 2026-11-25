$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
$root = Split-Path -Parent $PSScriptRoot
$commandPath = Join-Path $root 'dist/windows-command.ps1'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($commandPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw [string]$parseErrors[0] }
$command = [scriptblock]::Create([IO.File]::ReadAllText($commandPath))
$fixture = Join-Path $root 'dist/Windows-OneClick.ps1'
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('G1Bootstrap-' + [guid]::NewGuid())
$null = New-Item -ItemType Directory -Path $temporary
$oldTemp = $env:TEMP
$env:TEMP = $temporary

function Invoke-WebRequest {
    [CmdletBinding()]
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    $script:target = $OutFile
    if ($script:mode -eq 'http404') { Write-Error 'HTTP 404: Not Found'; return }
    if ($script:mode -eq 'network') { Write-Error 'Connection failed'; return }
    if ($script:mode -eq 'missing') { return }
    if ($script:mode -eq 'corrupt') { [IO.File]::WriteAllText($OutFile, 'corrupted'); return }
    [IO.File]::Copy($fixture, $OutFile)
}
function Get-FileHash {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$Algorithm)
    $script:hashCalls++
    Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
}
function powershell.exe {
    $script:launchCalls++
    $global:LASTEXITCODE = if ($script:mode -eq 'nativeFailure') { 7 } else { 0 }
}

try {
    foreach ($mode in @('http404', 'network', 'missing', 'corrupt', 'success', 'nativeFailure')) {
        $script:mode = $mode
        $script:hashCalls = 0; $script:launchCalls = 0; $script:target = $null
        $failure = $null
        try { & $command }
        catch { $failure = $_.Exception.Message }
        if ($mode -eq 'success') {
            if ($failure -or $script:hashCalls -ne 1 -or $script:launchCalls -ne 1) { throw 'Success path failed.' }
        }
        elseif ($mode -eq 'nativeFailure') {
            if ($failure -notlike '*code 7*' -or $script:launchCalls -ne 1) { throw 'Native failure not propagated.' }
        }
        else {
            if (-not $failure -or $script:launchCalls) { throw "Unsafe launch after $mode" }
            if ($mode -in @('http404', 'network', 'missing') -and $script:hashCalls) { throw "Hash called after $mode" }
            if ($mode -eq 'http404' -and $failure -notlike '*404*') { throw 'HTTP error masked.' }
            if ($mode -eq 'corrupt' -and $failure -notlike '*Checksum mismatch*') { throw 'Corruption error missing.' }
        }
        if (Test-Path -LiteralPath $script:target) { throw 'Temporary file was not cleaned up.' }
    }
    'PowerShell bootstrap: syntax and 6 regression checks passed; no real download or installer was executed.'
}
finally {
    $env:TEMP = $oldTemp
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
