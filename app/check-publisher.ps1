#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$Installer)
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1')) -ErrorAction Stop
$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $Installer
if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or $signature.SignerCertificate.Subject -notmatch '(?i)Netease|网易') {
    Write-Error 'MuMu installer publisher validation failed.'
    exit 1
}
Write-Output 'MuMu publisher verified.'
