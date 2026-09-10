# Two read-only provider GETs. SSH credentials stay in the user's terminal.
[CmdletBinding()]
param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$alphaProbePath = Join-Path $PSScriptRoot 'probe_stock_provider.py'
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'output\profitability'
}
$alphaStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$alphaOutputPath = Join-Path $OutputDirectory "provider-probe-$alphaStamp.json"

function Assert-ProbeKeys($Value, [string[]]$Expected) {
    if ($null -eq $Value -or $Value -isnot [PSCustomObject]) { throw 'Unerwartetes Diagnoseschema.' }
    $alphaNames = @($Value.PSObject.Properties.Name)
    if ($alphaNames.Count -ne $Expected.Count) { throw 'Unerwartetes Diagnoseschema.' }
    foreach ($alphaName in $alphaNames) {
        if ($Expected -cnotcontains $alphaName) { throw 'Unerwartetes Diagnoseschema.' }
    }
}

function Assert-ProbeCount($Value) {
    if (($Value -isnot [int] -and $Value -isnot [long]) -or $Value -lt 0 -or $Value -gt 20000) {
        throw 'Ungueltiger Diagnosezaehler.'
    }
}

if (-not (Test-Path -LiteralPath $alphaProbePath -PathType Leaf)) { throw 'Lokales Probe-Skript fehlt.' }
if (Test-Path -LiteralPath $alphaOutputPath) { throw 'Ausgabedatei existiert; nichts ueberschrieben.' }
Get-Command ssh -ErrorAction Stop | Out-Null
$alphaSource = Get-Content -Raw -LiteralPath $alphaProbePath
if ($alphaSource -match '[^\x00-\x7F]') { throw 'Probe-Skript muss fuer SSH-stdin ASCII bleiben.' }
Write-Host 'Nur lesende Diagnose: genau zwei Provider-Abfragen bei verfuegbarer Konfiguration.'
Write-Host 'Keine Scans, Testmails, Orders, Serverdateien, Dienste- oder DB-Aenderungen.'
Write-Host 'Bitte SSH-Passwort ausschliesslich in diesem Terminal eingeben, nicht im Chat.'
$alphaLines = $alphaSource | & ssh -T -o StrictHostKeyChecking=yes -o ConnectTimeout=10 root@178.104.69.209 '/usr/bin/python3 -I -'
if ($LASTEXITCODE -ne 0) { throw 'SSH-Diagnose fehlgeschlagen; keine Ergebnisdatei gespeichert.' }
$alphaJson = $alphaLines -join "`n"
if ($alphaJson.Length -gt 32768) { throw 'Diagnoseantwort zu gross; nichts gespeichert.' }
try { $alphaEvidence = $alphaJson | ConvertFrom-Json } catch { throw 'Ungueltige Diagnoseantwort; nichts gespeichert.' }
Assert-ProbeKeys $alphaEvidence @('schema_version', 'kind', 'read_only', 'captured_at', 'service_identity', 'config', 'endpoints')
if ($alphaEvidence.schema_version -isnot [int] -or $alphaEvidence.schema_version -ne 1 -or
    $alphaEvidence.kind -cne 'stock_provider_probe' -or $alphaEvidence.read_only -isnot [bool] -or
    $alphaEvidence.read_only -ne $true -or
    @('verified', 'changed', 'unavailable') -cnotcontains $alphaEvidence.service_identity) {
    throw 'Unerwartete Diagnoseantwort; nichts gespeichert.'
}
$alphaCaptured = [DateTimeOffset]::MinValue
if ($alphaEvidence.captured_at -isnot [string] -or $alphaEvidence.captured_at -notmatch '(Z|\+00:00)$' -or
    -not [DateTimeOffset]::TryParse($alphaEvidence.captured_at, [ref]$alphaCaptured)) { throw 'Ungueltige Diagnosezeit.' }
Assert-ProbeKeys $alphaEvidence.config @('status', 'source', 'binding')
if (@('available', 'unavailable', 'missing') -cnotcontains $alphaEvidence.config.status -or
    @('none', 'startup_environment', 'app_env', 'app_secrets', 'service_home_secrets') -cnotcontains $alphaEvidence.config.source -or
    $alphaEvidence.config.binding -cne 'uncertain') { throw 'Ungueltiger Konfigurationsstatus.' }
Assert-ProbeKeys $alphaEvidence.endpoints @('full_snapshot', 'liquid_control')
$alphaFields = @('lastTrade.p', 'lastTrade.t', 'lastQuote.bid_price', 'lastQuote.ask_price', 'lastQuote.t', 'day.c', 'prevDay.c', 'min.c', 'min.t', 'updated')
$alphaStates = @('missing', 'null', 'zero', 'negative', 'bool', 'nonfinite', 'string', 'other', 'positive')
$alphaStatuses = @('ok', 'delayed', 'not_requested', 'invalid_payload', 'provider_error', 'too_many_rows', 'too_large', 'invalid_json', 'redirect', 'unauthorized', 'forbidden', 'rate_limited', 'server_error', 'client_error', 'tls_error', 'timeout', 'connection_error', 'unexpected_error')
foreach ($alphaEndpoint in @('full_snapshot', 'liquid_control')) {
    $alphaResult = $alphaEvidence.endpoints.$alphaEndpoint
    Assert-ProbeKeys $alphaResult @('status', 'rows', 'fields')
    if ($alphaStatuses -cnotcontains $alphaResult.status) { throw 'Ungueltiger Providerstatus.' }
    Assert-ProbeCount $alphaResult.rows
    if (@('ok', 'delayed') -cnotcontains $alphaResult.status -and $alphaResult.rows -ne 0) { throw 'Fehlerantwort mit Ergebniszeilen.' }
    Assert-ProbeKeys $alphaResult.fields $alphaFields
    foreach ($alphaField in $alphaFields) {
        $alphaCounts = $alphaResult.fields.$alphaField
        Assert-ProbeKeys $alphaCounts $alphaStates
        $alphaTotal = 0
        foreach ($alphaState in $alphaStates) {
            Assert-ProbeCount $alphaCounts.$alphaState
            $alphaTotal += $alphaCounts.$alphaState
        }
        if ($alphaTotal -ne $alphaResult.rows) { throw 'Inkonsistente Diagnosezaehler.' }
    }
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
# Save only the validated projection, never unvalidated duplicate/raw JSON text.
$alphaSafeJson = $alphaEvidence | ConvertTo-Json -Depth 8 -Compress
$alphaUtf8 = New-Object System.Text.UTF8Encoding($false)
$alphaStream = [System.IO.File]::Open($alphaOutputPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $alphaBytes = $alphaUtf8.GetBytes($alphaSafeJson)
    $alphaStream.Write($alphaBytes, 0, $alphaBytes.Length)
} finally { $alphaStream.Dispose() }
Write-Host "Private Diagnose gespeichert: $alphaOutputPath"
Write-Host 'Nur feste Status-/Feldzaehler; keine Symbole, Preise, Schluessel oder Providertexte.'
Write-Host 'Konfigurationsbindung bleibt unsicher: kein Beweis des gecachten Runtime-Schluessels.'
Write-Host 'Bitte Codex melden: Provider-Diagnose ist fertig. Kein Serverupdate erforderlich.'
