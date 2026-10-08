# Run locally. SSH reads the reviewed stdlib source; the password stays in the terminal.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$momentumReaderPath = Join-Path $PSScriptRoot 'collect_momentum_candidate_evidence.py'
$momentumRepoPath = Split-Path -Parent $PSScriptRoot
$momentumOutputDir = Join-Path $momentumRepoPath 'output\profitability'
$momentumStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$momentumOutputPath = Join-Path $momentumOutputDir "momentum-candidate-evidence-$momentumStamp.json"

if (-not (Test-Path -LiteralPath $momentumReaderPath -PathType Leaf)) {
    throw 'Der lokale Momentum-Reader fehlt.'
}
if (Test-Path -LiteralPath $momentumOutputPath) {
    throw 'Ausgabedatei existiert bereits; nichts ueberschrieben.'
}
Get-Command ssh -ErrorAction Stop | Out-Null
Write-Host 'Nur gespeicherte RELL/UVE/NECB/GKOS-Plan-, Liquiditaets- und Zonenbelege.'
Write-Host 'Keine Kontodaten, Providerabrufe, Scans, Mails, Pulls oder Serveraenderungen.'
Write-Host 'Passwort nur in diesem Terminal eingeben; es wird nicht gespeichert.'
$momentumSource = Get-Content -Raw -LiteralPath $momentumReaderPath
$momentumLines = $momentumSource | & ssh -T -o StrictHostKeyChecking=yes -o ConnectTimeout=10 root@178.104.69.209 '/usr/bin/python3 -I -'
if ($LASTEXITCODE -ne 0) {
    throw 'Momentum-Export fehlgeschlagen. Keine Ergebnisdatei gespeichert.'
}
$momentumJson = $momentumLines -join "`n"
$momentumUtf8 = New-Object System.Text.UTF8Encoding($false)
$momentumBytes = $momentumUtf8.GetBytes($momentumJson)
if ($momentumBytes.Length -gt (2 * 1024 * 1024)) {
    throw 'Momentum-Antwort zu gross. Keine Ergebnisdatei gespeichert.'
}
try {
    # Windows PowerShell 5 treats JSON property names as case-insensitive.
    # The original ticker/Ticker and score/Score aliases must remain intact.
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('AsHashtable')) {
        $momentumEvidence = $momentumJson | ConvertFrom-Json -AsHashtable
    } else {
        Add-Type -AssemblyName System.Web.Extensions
        $momentumParser = New-Object System.Web.Script.Serialization.JavaScriptSerializer
        $momentumParser.MaxJsonLength = 2 * 1024 * 1024
        $momentumParser.RecursionLimit = 64
        $momentumEvidence = $momentumParser.DeserializeObject($momentumJson)
    }
} catch {
    throw 'Ungueltige Momentum-JSON-Antwort. Keine Ergebnisdatei gespeichert.'
}
$momentumExpectedTickers = @('RELL', 'UVE', 'NECB', 'GKOS')
if ($momentumEvidence.kind -cne 'momentum_candidate_evidence' -or
    $momentumEvidence.read_only -isnot [bool] -or $momentumEvidence.read_only -ne $true -or
    ($momentumEvidence.schema_version -isnot [int] -and $momentumEvidence.schema_version -isnot [long]) -or
    $momentumEvidence.schema_version -ne 1 -or $momentumEvidence.status -cne 'ok' -or
    $momentumEvidence.semantics -cne 'stored_momentum_candidates_not_fresh_approval_or_mail_delivery' -or
    @($momentumEvidence.candidates).Count -ne 4 -or
    $momentumEvidence.raw_daily_prefix.provider_fetch_performed -isnot [bool] -or
    $momentumEvidence.raw_daily_prefix.provider_fetch_performed -ne $false) {
    throw 'Unerwartete Momentum-Antwort. Keine Ergebnisdatei gespeichert.'
}
for ($momentumIndex = 0; $momentumIndex -lt 4; $momentumIndex++) {
    $momentumCandidate = $momentumEvidence.candidates[$momentumIndex]
    if ($momentumCandidate.ticker -cne $momentumExpectedTickers[$momentumIndex] -or
        $momentumCandidate.status -cnotin @('ok', 'not_found', 'not_unique', 'invalid', 'ticker_alias_conflict')) {
        throw 'Unerwartete Kandidatenidentitaet. Keine Ergebnisdatei gespeichert.'
    }
}
New-Item -ItemType Directory -Path $momentumOutputDir -Force | Out-Null
$momentumStream = [System.IO.File]::Open($momentumOutputPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $momentumStream.Write($momentumBytes, 0, $momentumBytes.Length)
} finally {
    $momentumStream.Dispose()
}
Write-Host "Momentum-Evidenz gespeichert: $momentumOutputPath"
Write-Host 'Privater Diagnosebeleg; nicht auf GitHub hochladen. Kein Serverupdate erforderlich.'
