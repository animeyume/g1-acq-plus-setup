$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
$source = Join-Path (Split-Path -Parent $PSScriptRoot) 'install/windows.ps1'
$tokens=$null; $parseErrors=$null
$null=[System.Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw ($parseErrors|Out-String)}
. $source
$failed = $false
try { Invoke-CheckedNative -Program '/definitely/missing/python.exe' -NativeArgs @('--version') }
catch { $failed = $_.Exception.Message -like '*Исполняемый файл не найден*' }
if(-not $failed){throw 'Missing executable guard failed.'}

$original = ${function:Invoke-CheckedNative}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('G1 Runtime Кирилл ' + [guid]::NewGuid())
$null=New-Item -ItemType Directory -Path $temporary
$script:history=@()
$script:basePython=Join-Path $temporary 'actual managed python.exe'
$script:createVenv=$true
$script:root=$temporary
function Invoke-CheckedNative {
    param([string]$Program,[string[]]$NativeArgs,[switch]$Capture)
    $script:history += ,$NativeArgs
    if($NativeArgs -contains 'find'){return $script:basePython}
    if($NativeArgs -contains 'venv' -and $script:createVenv){
        $folder=Join-Path $script:root 'venv/Scripts'
        $null=New-Item -ItemType Directory -Path $folder -Force
        $null=New-Item -ItemType File -Path (Join-Path $folder 'python.exe') -Force
    }
}
try {
    $failed=$false
    try { Initialize-G1Runtime -Root $temporary -Uv 'uv.exe' }
    catch { $failed=$_.Exception.Message -like '*uv не вернул существующий Python*' }
    if(-not $failed){throw 'Missing Python after download was not detected.'}
    if(@($script:history|Where-Object {$_ -contains 'venv'}).Count){throw 'A missing Python was used for venv.'}
    $null=New-Item -ItemType File -Path $script:basePython
    $script:createVenv=$false
    $failed=$false
    try { Initialize-G1Runtime -Root $temporary -Uv 'uv.exe' }
    catch { $failed=$_.Exception.Message -like '*не создало python.exe*' }
    if(-not $failed){throw 'An incomplete venv was not detected.'}
    $script:createVenv=$true
    $actual=Initialize-G1Runtime -Root $temporary -Uv 'uv.exe'
    if(-not(Test-Path -LiteralPath $actual -PathType Leaf)){throw 'Valid runtime did not resolve.'}
    if(-not(@($script:history|Where-Object {($_ -contains '--no-bin') -and ($_ -contains '--no-registry')}).Count)){throw 'Registry/PATH isolation flags missing.'}
    'PowerShell: parser and 4 runtime regression checks passed.'
}
finally {
    ${function:Invoke-CheckedNative}=$original
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
