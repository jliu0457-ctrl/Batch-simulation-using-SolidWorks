$ErrorActionPreference = 'Stop'
$mappingRoot = Split-Path -LiteralPath $PSScriptRoot -Parent
$trialFolder = Join-Path $mappingRoot 'working\seven_variable_trials\capless_v6_changed_012'
$output = Join-Path $trialFolder 'mate_dimension_inspection.txt'
$errorLog = Join-Path $trialFolder 'mate_dimension_inspection_error.txt'
try {
    $assembly = Get-ChildItem -LiteralPath $trialFolder -Filter '*.SLDASM' -File | Select-Object -First 1 -ExpandProperty FullName
    if (-not $assembly) { throw '012 assembly was not found.' }
    & (Join-Path $PSScriptRoot 'Inspect012Mates.exe') $assembly $output
    if ($LASTEXITCODE -ne 0) { throw "Inspector exited with code $LASTEXITCODE." }
    exit 0
}
catch {
    [IO.File]::WriteAllText($errorLog, $_ | Out-String, [Text.UTF8Encoding]::new($false))
    exit 1
}
