# Run locally in PowerShell. SSH asks for the user's password in their terminal.
# Runs the reviewed stdlib-only collector via stdin; no server file is uploaded.
# Never imports service-owned application code as root or changes server state.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$alphaCollectorPath = Join-Path $PSScriptRoot 'collect_server_evidence.py'
$alphaRepoPath = Split-Path -Parent $PSScriptRoot
$alphaOutputDir = Join-Path $alphaRepoPath 'output\profitability'
$alphaStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$alphaOutputPath = Join-Path $alphaOutputDir "hetzner-evidence-$alphaStamp.json"

if (-not (Test-Path -LiteralPath $alphaCollectorPath -PathType Leaf)) {
    throw 'Collector fehlt. Bitte aus dem lokalen TradingBot-Projekt starten.'
}
if (Test-Path -LiteralPath $alphaOutputPath) {
    throw 'Ausgabedatei existiert bereits; nichts ueberschrieben.'
}
Get-Command ssh -ErrorAction Stop | Out-Null
Write-Host 'Nur lesender Export: keine Scans, kein Pull, keine Dienste-/DB-Aenderung.'
Write-Host 'Bitte dein SSH-Passwort in diesem Fenster eingeben; es wird nicht gespeichert.'
$alphaSource = Get-Content -Raw -LiteralPath $alphaCollectorPath
# Source uses ASCII only. The SSH password prompt uses the terminal, not piped stdin.
$alphaLines = $alphaSource | & ssh -T -o StrictHostKeyChecking=yes -o ConnectTimeout=10 root@178.104.69.209 '/usr/bin/python3 -I -'
if ($LASTEXITCODE -ne 0) {
    throw 'Serverexport fehlgeschlagen. Keine Ergebnisdatei gespeichert.'
}
$alphaJson = $alphaLines -join "`n"
$alphaEvidence = $alphaJson | ConvertFrom-Json
if ($alphaEvidence.kind -ne 'private_server_evidence' -or $alphaEvidence.read_only -ne $true -or $alphaEvidence.schema_version -ne 1) {
    throw 'Unerwartete Serverantwort. Keine Ergebnisdatei gespeichert.'
}
New-Item -ItemType Directory -Path $alphaOutputDir -Force | Out-Null
$alphaUtf8 = New-Object System.Text.UTF8Encoding($false)
$alphaStream = [System.IO.File]::Open($alphaOutputPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $alphaBytes = $alphaUtf8.GetBytes($alphaJson)
    $alphaStream.Write($alphaBytes, 0, $alphaBytes.Length)
} finally {
    $alphaStream.Dispose()
}
Write-Host "Export gespeichert: $alphaOutputPath"
Write-Host 'Enthaelt private, auf Auditfelder begrenzte Signalzeilen. Nicht auf GitHub hochladen.'
Write-Host 'Bitte Codex melden: Export ist fertig. Ein Serverupdate ist dafuer nicht erforderlich.'
