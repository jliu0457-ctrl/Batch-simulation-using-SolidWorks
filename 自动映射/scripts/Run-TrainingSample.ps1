param(
    [Parameter(Mandatory = $true)][string]$RunName,
    [Parameter(Mandatory = $true)][string]$InputFile,
    [string]$DatasetXlsx,
    [double]$DensityKgM3 = 1000.0,
    [double]$TimeoutMinutes = 120.0,
    [switch]$ValidateFlowOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

$mappingRoot = Split-Path (Split-Path $PSCommandPath -Parent) -Parent
$projectRoot = Split-Path $mappingRoot -Parent
$cadScript = Join-Path $PSScriptRoot 'Apply-SevenVariableDesign.ps1'
$flowScript = Join-Path $PSScriptRoot 'Run-FlowSingle.py'
if (-not $DatasetXlsx) { $DatasetXlsx = Join-Path $mappingRoot 'outputs\training_dataset.xlsx' }

$summary = [ordered]@{
    schema_version = 1
    run_name = $RunName
    started_utc = [DateTime]::UtcNow.ToString('o')
    input_file = [IO.Path]::GetFullPath($InputFile)
    dataset_xlsx = [IO.Path]::GetFullPath($DatasetXlsx)
    status = 'starting'
    cad = $null
    flow = $null
}
$runFolder = Join-Path $mappingRoot ('working\seven_variable_trials\' + $RunName)
$summaryPath = Join-Path $runFolder 'pipeline_result.json'

function Save-Summary {
    if (Test-Path -LiteralPath $runFolder) {
        [IO.File]::WriteAllText($summaryPath, ($summary | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
    }
}

try {
    $summary.status = 'cad_parameterization_running'
    $cadText = (& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $cadScript -RunName $RunName -InputFile $InputFile | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "CAD parameterization failed: $cadText" }
    $cad = ConvertFrom-Json -InputObject $cadText
    $summary.cad = $cad
    if ($cad.status -ne 'geometry_verified' -or $cad.completed -ne $true -or $cad.physical_mapping_verified -ne $true) {
        throw "CAD parameterization did not reach geometry_verified: $cadText"
    }
    Save-Summary

    $summary.status = $(if ($ValidateFlowOnly) { 'flow_validation_running' } else { 'flow_solve_running' })
    Save-Summary
    $flowArgs = @($flowScript, $runFolder, '--rho-kg-m3', $DensityKgM3.ToString([Globalization.CultureInfo]::InvariantCulture),
                  '--timeout-min', $TimeoutMinutes.ToString([Globalization.CultureInfo]::InvariantCulture),
                  '--dataset-xlsx', $DatasetXlsx)
    if ($ValidateFlowOnly) { $flowArgs += '--validate-flow-rebuild' } else { $flowArgs += '--solve' }
    $flowText = (& py -3 @flowArgs | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Flow stage failed: $flowText" }
    $flow = ConvertFrom-Json -InputObject $flowText
    $summary.flow = $flow
    $expectedStatus = $(if ($ValidateFlowOnly) { 'flow_rebuild_verified' } else { 'solve_and_export_completed' })
    if ($flow.status -ne $expectedStatus) { throw "Unexpected Flow status: $flowText" }

    $summary.status = $(if ($ValidateFlowOnly) { 'flow_validation_completed' } else { 'training_sample_completed' })
    $summary.completed = $true
}
catch {
    $summary.status = 'failed'
    $summary.completed = $false
    $summary.error = $_.Exception.ToString()
    throw
}
finally {
    $summary.finished_utc = [DateTime]::UtcNow.ToString('o')
    Save-Summary
    $summary | ConvertTo-Json -Depth 20
}
