# Run locally in PowerShell; SSH asks for the user's password in their terminal.
# Streams reviewed stdlib-only source. No upload, pull, scan or server writes.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$lspdCollectorPath = Join-Path $PSScriptRoot 'collect_lspd_plan_evidence.py'
$lspdRepoPath = Split-Path -Parent $PSScriptRoot
$lspdOutputDir = Join-Path $lspdRepoPath 'output\profitability'
$lspdStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$lspdOutputPath = Join-Path $lspdOutputDir "lspd-plan-evidence-$lspdStamp.json"

if (-not (Test-Path -LiteralPath $lspdCollectorPath -PathType Leaf)) {
    throw 'LSPD-Reader fehlt. Bitte aus dem lokalen TradingBot-Projekt starten.'
}
if (Test-Path -LiteralPath $lspdOutputPath) {
    throw 'Ausgabedatei existiert bereits; nichts ueberschrieben.'
}
Get-Command ssh -ErrorAction Stop | Out-Null
Write-Host 'Nur gespeicherte LSPD-Plan-/Zonenfelder; keine Kurse, Scans oder Serveraenderungen.'
Write-Host 'Originale Tageskerzen fehlen in diesem Cache; kein Providerabruf.'
Write-Host 'Bitte dein SSH-Passwort in diesem Fenster eingeben; es wird nicht gespeichert.'
$lspdSource = Get-Content -Raw -LiteralPath $lspdCollectorPath
# Source is ASCII. The password prompt uses the terminal, not piped stdin.
$lspdLines = $lspdSource | & ssh -T -o StrictHostKeyChecking=yes -o ConnectTimeout=10 root@178.104.69.209 '/usr/bin/python3 -I -'
if ($LASTEXITCODE -ne 0) {
    throw 'LSPD-Export fehlgeschlagen. Keine Ergebnisdatei gespeichert.'
}
$lspdJson = $lspdLines -join "`n"
$lspdEvidence = $lspdJson | ConvertFrom-Json
if ($lspdEvidence.kind -cne 'lspd_plan_evidence' -or
    $lspdEvidence.read_only -isnot [bool] -or $lspdEvidence.read_only -ne $true -or
    $lspdEvidence.schema_version -ne 1 -or $lspdEvidence.status -cne 'ok' -or
    $lspdEvidence.semantics -cne 'stored_lspd_plan_and_zones_not_fresh_approval_or_mail_delivery' -or
    $lspdEvidence.lspd.ticker -cne 'LSPD' -or
    $lspdEvidence.raw_daily_prefix.provider_fetch_performed -isnot [bool] -or
    $lspdEvidence.raw_daily_prefix.provider_fetch_performed -ne $false) {
    throw 'Unerwartete LSPD-Antwort. Keine Ergebnisdatei gespeichert.'
}
$lspdUtf8 = New-Object System.Text.UTF8Encoding($false)
$lspdBytes = $lspdUtf8.GetBytes($lspdJson)
if ($lspdBytes.Length -gt (2 * 1024 * 1024)) {
    throw 'LSPD-Antwort zu gross. Keine Ergebnisdatei gespeichert.'
}
New-Item -ItemType Directory -Path $lspdOutputDir -Force | Out-Null
# CreateNew also closes a race after the earlier existence check.
$lspdStream = [System.IO.File]::Open($lspdOutputPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $lspdStream.Write($lspdBytes, 0, $lspdBytes.Length)
} finally {
    $lspdStream.Dispose()
}
Write-Host "LSPD-Evidenz gespeichert: $lspdOutputPath"
Write-Host 'Private Diagnose: nur erlaubte native Felder, keine Kontodaten. Nicht auf GitHub hochladen.'
Write-Host 'Gespeicherte Evidenz, keine aktuelle Tradefreigabe oder Zustellbestaetigung.'
Write-Host 'Bitte Codex melden: LSPD-Export ist fertig. Kein Serverupdate erforderlich.'
