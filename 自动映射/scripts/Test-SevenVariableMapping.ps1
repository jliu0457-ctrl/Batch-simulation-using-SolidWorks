param(
    [string]$Root = (Split-Path $PSScriptRoot -Parent),
    [Alias('Template')]
    [string]$TemplateFolder = '',
    [string]$RunName = '',
    [string]$Interop = 'D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll',
    [ValidateSet('baseline', 'three', 'full')]
    [string]$Suite = 'three',
    [switch]$FullSuite,
    [switch]$CaliperSelfTest,
    [switch]$CompileOnly
)
$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath($Root)
$authorized = [IO.Path]::GetFullPath((Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)).TrimEnd('\', '/') + '\'
if (-not $Root.StartsWith($authorized, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Root must remain inside the workspace that contains 自动映射.'
}
if (-not $TemplateFolder) { $TemplateFolder = Join-Path $Root 'working\assembly_repair_v4' }
if (-not $RunName) { $RunName = 'physical_' + [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss') + '_' + [Guid]::NewGuid().ToString('N').Substring(0, 6) }
$compileTemp = Join-Path $Root 'artifacts\compile_temp'
New-Item -ItemType Directory -Path $compileTemp -Force | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $env:TEMP = $compileTemp
    $env:TMP = $compileTemp
    [Reflection.Assembly]::LoadFrom($Interop) | Out-Null
    Add-Type -Path (Join-Path $PSScriptRoot 'SevenVariableAdapter.cs') -ReferencedAssemblies @($Interop, 'System.Web.Extensions', 'System.Core')
    if ($CaliperSelfTest) {
        $mathResult = [SevenVariableAdapter]::CaliperSelfTest()
        $mathResult | Add-Member -NotePropertyName adapter_source_sha256 -NotePropertyValue (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'SevenVariableAdapter.cs') -Algorithm SHA256).Hash
        $mathReport = Join-Path $Root 'artifacts\caliper_selftest_v2.json'
        $mathResult | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $mathReport -Encoding UTF8
        [ordered]@{ status = $mathResult.status; count = $mathResult.count; cad_executed = $false; report = $mathReport } | ConvertTo-Json
        return
    }
    if ($CompileOnly) {
        [ordered]@{ status = 'compiled_only'; cad_executed = $false; training_ready = $false } | ConvertTo-Json
        return
    }
    if ($FullSuite) { $Suite = 'full' }
    $result = [SevenVariableAdapter]::RunSuite($Root, $TemplateFolder, $RunName, $Suite)
    [ordered]@{
        status = $result.status
        run_name = $result.run_name
        report_folder = $result.folder
        completed_trials = $result.completed_trials
        all_requested_geometry_trials_passed = $result.all_requested_geometry_trials_passed
        training_ready = $false
        error = $result.error
    } | ConvertTo-Json -Depth 5
}
finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}
