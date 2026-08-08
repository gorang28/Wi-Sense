param(
    [string]$Port = 'COM13',
    [int]$DurationSeconds = 120,
    [int]$BreakSeconds = 15,
    [int]$Count = 20
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$captureScript = Join-Path $projectRoot 'python\capture\capture_csi_binary.py'
$outputDir = Join-Path $projectRoot 'data\dataset\empty_fan_on'
$runStamp = Get-Date -Format 'yyyyMMdd_HHmmss'

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$env:PYTHONPATH = Join-Path $projectRoot 'python'

Write-Host "Capturing $Count empty-room windows from $Port"
Write-Host "Each capture: $DurationSeconds s | Break: $BreakSeconds s"
Write-Host 'Keep the room empty and close any serial monitor or live detector.'

for ($index = 1; $index -le $Count; $index++) {
    $id = $index.ToString('00')
    $baseName = "live_empty_$runStamp`_$id"
    $csvPath = Join-Path $outputDir "$baseName.csv"
    $logPath = Join-Path $outputDir "$baseName.log"

    Write-Host "[$index/$Count] Recording $baseName"
    & python $captureScript -p $Port -s $csvPath -l $logPath --duration $DurationSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "Capture $index failed with exit code $LASTEXITCODE."
    }

    if ($index -lt $Count) {
        Write-Host "Break for $BreakSeconds seconds..."
        Start-Sleep -Seconds $BreakSeconds
    }
}

Write-Host 'All empty-room captures completed.'
