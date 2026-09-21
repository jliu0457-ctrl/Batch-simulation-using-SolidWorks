<#
.SYNOPSIS
Apply one seven-variable design to an isolated copy of the verified v4 valve.
.DESCRIPTION
Use Windows PowerShell 5.1 (64 bit). This entry performs CAD geometry only.
Inputs: c_mm, e_mm, phi_deg, alpha_deg, Dmax_mm, bm_mm, ds_mm.
Dmax_mm is the physical maximum projected disc caliper, not the CAD cone circle.
-ValidateOnly checks JSON, paths and the pinned template; it writes nothing.
-CompileOnly compiles the adapter and bridge without accessing SolidWorks.
.EXAMPLE
.\Apply-SevenVariableDesign.ps1 -InputFile .\design.json -RunName joint_plus_v1 -ValidateOnly
.EXAMPLE
.\Apply-SevenVariableDesign.ps1 -InputFile .\design.json -RunName joint_plus_v1
#>
[CmdletBinding()]
param(
    [Alias('InputJson', 'DesignJson')][string]$InputFile = '',
    [string]$RunName = '',
    [string]$Root = '',
    [string]$Interop = 'D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll',
    [switch]$ValidateOnly,
    [switch]$CompileOnly,
    [switch]$RepairOnlyDiagnostic
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
# Keep all generated files inside the workspace that contains this checked-in
# 自动映射 directory.  Deriving this boundary from the script location makes
# the entry point portable across Windows user profiles and workspace moves.
$taskRootFromScript = Split-Path $PSScriptRoot -Parent
$authorized = Split-Path $taskRootFromScript -Parent
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$runFolder = $null
$ownsRunFolder = $false
$sourceFiles = @()
$templateBefore = $null
$exitCode = 0
$report = [ordered]@{
    wrapper_version = 'single_design_v2'
    status = 'preflight'
    started_utc = [DateTime]::UtcNow.ToString('o')
    completed = $false
    physical_mapping_verified = $false
    cad_executed = $false
    training_ready = $false
    simulation_labels_generated = $false
}

function Assert-SafePath([string]$Path, [string]$Parent, [bool]$MustExist = $false) {
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $fullParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    if (-not $fullPath.StartsWith($fullParent + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path must remain below its authorized parent: $fullPath"
    }
    # Inspect every existing ancestor, including ancestors above the project.
    $cursor = $fullPath
    while ($cursor) {
        try {
            $attributes = [IO.File]::GetAttributes($cursor)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed in CAD task paths: $cursor"
            }
        } catch [IO.FileNotFoundException] {
        } catch [IO.DirectoryNotFoundException] {
        }
        $next = [IO.Path]::GetDirectoryName($cursor)
        if ($next -eq $cursor) { break }
        $cursor = $next
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $fullPath)) { throw "Path does not exist: $fullPath" }
    return $fullPath
}
function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}
function Get-Hashes($Files) {
    $hashes = [ordered]@{}
    foreach ($file in $Files) {
        $hashes[$file.Name] = (Get-Sha256 $file.FullName)
    }
    return $hashes
}
function Assert-HashesEqual($Expected, $Actual, [string]$Context) {
    if ($Expected.Count -ne $Actual.Count) { throw "$Context file count differs." }
    foreach ($name in $Expected.Keys) {
        if (-not $Actual.Contains($name) -or $Expected[$name] -cne $Actual[$name]) {
            throw "$Context hash differs: $name"
        }
    }
}
function Write-JsonReport([string]$Path, $Value) {
    $json = ConvertTo-Json -InputObject $Value -Depth 100
    [IO.File]::WriteAllText($Path, $json, [Text.UTF8Encoding]::new($false))
}
try {
    if (-not $Root) { $Root = Split-Path (Split-Path $PSCommandPath -Parent) -Parent }
    if ($ValidateOnly -and $CompileOnly) { throw 'Use ValidateOnly or CompileOnly, not both.' }
    $Root = Assert-SafePath $Root $authorized $true
    if ([IO.Path]::GetFileName($Root) -cne '自动映射') { throw 'Expected the verified 自动映射 task root.' }
    $sourcePath = Assert-SafePath (Join-Path $PSScriptRoot 'SevenVariableAdapter.cs') $Root $true
    $repairSourcePath = Assert-SafePath (Join-Path $PSScriptRoot 'RepairAssembly.cs') $Root $true
    $template = Assert-SafePath (Join-Path $Root 'working\assembly_batch_v6') $Root $false
    $templateManifestPath = Assert-SafePath (Join-Path $Root 'config\cad_template_manifest_v6.json') $Root $false
    $trialsParent = Assert-SafePath (Join-Path $Root 'working\seven_variable_trials') $Root
    $pinned = [ordered]@{}
    if (-not $CompileOnly) {
        $null = Assert-SafePath $template $Root $true
        $null = Assert-SafePath $templateManifestPath $Root $true
        # v6 is the user-validated four-cap internal-flow template.
        $templateManifest = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($templateManifestPath))
        if ($templateManifest.template_id -cne 'sealed_flow_capless_v6' -or
            [int]$templateManifest.expected_assemblies -ne 1 -or
            [int]$templateManifest.expected_external_parts -ne 6 -or
            [double]$templateManifest.fixed_opening_deg -ne 45) {
            throw 'Invalid four-cap v6 template manifest.'
        }
        foreach ($property in $templateManifest.files.PSObject.Properties) {
            if ($property.Name -ne [IO.Path]::GetFileName($property.Name) -or $property.Value -cnotmatch '^[0-9a-f]{64}$') {
                throw "Invalid v6 template manifest entry: $($property.Name)"
            }
            $pinned[$property.Name] = [string]$property.Value
        }
        if ($pinned.Count -ne 7) { throw 'The capless v6 manifest must pin seven CAD files (assembly + six core parts).' }
        $sourceFiles = @(Get-ChildItem -LiteralPath $template -File | Where-Object { $_.Extension -match '^\.SLD' } | Sort-Object Name)
        foreach ($file in $sourceFiles) { $null = Assert-SafePath $file.FullName $template $true }
        $templateBefore = Get-Hashes $sourceFiles
        Assert-HashesEqual $pinned $templateBefore 'Verified four-cap v6 template'
        $report.template_folder = $template
        $report.template_hashes_before = $templateBefore
    }
    $report.adapter_source_sha256 = (Get-Sha256 $sourcePath)
    $report.fixed45_repair_source_sha256 = (Get-Sha256 $repairSourcePath)
    $report.wrapper_source_sha256 = (Get-Sha256 $PSCommandPath)

    if (-not $CompileOnly) {
        if (-not $InputFile) { throw 'InputFile is required.' }
        if ($RunName -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$' -or $RunName -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
            throw 'RunName must be a unique ordinary name of 1-80 ASCII letters, digits, underscores or hyphens.'
        }
        $runFolder = Assert-SafePath (Join-Path $trialsParent $RunName) $trialsParent
        if (Test-Path -LiteralPath $runFolder) { throw "Refusing to overwrite an existing run: $runFolder" }
        $inputPath = [IO.Path]::GetFullPath($InputFile)
        $rawJson = [IO.File]::ReadAllText($inputPath)
        if ($rawJson.Length -gt 65536) { throw 'The seven-number input JSON is unexpectedly large.' }
        $designObject = ConvertFrom-Json -InputObject $rawJson
        if ($null -eq $designObject -or $designObject -is [Array]) { throw 'Input must be one flat JSON object.' }
        $names = @('c_mm', 'e_mm', 'phi_deg', 'alpha_deg', 'Dmax_mm', 'bm_mm', 'ds_mm')
        $properties = @($designObject.PSObject.Properties)
        $rawKeys = [regex]::Matches($rawJson, '"(?<key>[^"\\]*)"\s*:')
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        if ($properties.Count -ne 7 -or $rawKeys.Count -ne 7) { throw 'Exactly seven unescaped, uniquely named JSON fields are required.' }
        foreach ($key in $rawKeys) {
            $name = $key.Groups['key'].Value
            if (-not ($names -ccontains $name) -or -not $seen.Add($name)) { throw "Unexpected or duplicate input field: $name" }
        }
        $design = [ordered]@{}
        foreach ($name in $names) {
            $value = $designObject.$name
            if ($value -isnot [int] -and $value -isnot [long] -and $value -isnot [double] -and $value -isnot [decimal]) {
                throw "Input $name must be a JSON number."
            }
            $number = [double]$value
            if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) { throw "Input $name must be finite." }
            $design[$name] = $number
        }
        if ($design.c_mm -lt 0 -or $design.e_mm -lt 0 -or $design.Dmax_mm -le 0 -or $design.bm_mm -le 0 -or $design.ds_mm -le 0) {
            throw 'Invalid nonpositive geometry input.'
        }
        if ($design.bm_mm -ge $design.Dmax_mm -or $design.ds_mm -ge $design.Dmax_mm) { throw 'Seal thickness and shaft diameter must be smaller than Dmax.' }
        if ($design.phi_deg -le 0 -or $design.alpha_deg -le 0 -or $design.alpha_deg -ge 180 -or $design.alpha_deg / 2 + $design.phi_deg -ge 90) {
            throw 'Inputs are outside the audited two-angle CAD template domain.'
        }
        $normalizedJson = ConvertTo-Json -InputObject $design -Compress
        $report.run_name = $RunName
        $report.folder = $runFolder
        $report.input = $design
        $report.input_file = $inputPath
        $report.input_file_sha256 = (Get-Sha256 $inputPath)
        $report.validation_scope = 'Schema and audited CAD template domain only; no engineering feasibility or optimization-bound certification.'
        if ($ValidateOnly) {
            $report.status = 'validated_only'
            $report.template_unchanged = $true
            $report.finished_utc = [DateTime]::UtcNow.ToString('o')
            $report | ConvertTo-Json -Depth 10
            return
        }
    }
    if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit Windows PowerShell for SolidWorks 2026.' }
    if (-not (Test-Path -LiteralPath $Interop -PathType Leaf)) { throw "SolidWorks 2026 interop is missing: $Interop" }
    if ('SevenVariableAdapter' -as [type]) { throw 'Adapter is already loaded. Start a fresh powershell.exe process to avoid stale source.' }
    $compileTemp = Assert-SafePath (Join-Path $Root ('artifacts\compile_single_' + [Guid]::NewGuid().ToString('N'))) $Root
    New-Item -ItemType Directory -Path $compileTemp | Out-Null
    $env:TEMP = $compileTemp
    $env:TMP = $compileTemp
    [Reflection.Assembly]::LoadFrom($Interop) | Out-Null
    # Typed bridge keeps COM property access within the official C# interop.
    $bridgeSource = @'
public static class SingleDesignBridge
{
    public static object Invoke(string root, string folder, string json)
    {
        var report = new System.Collections.Generic.Dictionary<string, object>();
        SolidWorks.Interop.sldworks.ISldWorks app = null;
        bool original = false, captured = false;
        report["cad_invocation_started"] = false;
        report["command_in_progress_restored"] = false;
        try
        {
            object com = null;
            try { com = System.Runtime.InteropServices.Marshal.GetActiveObject("SldWorks.Application.34"); }
            catch (System.Runtime.InteropServices.COMException)
            {
                try { com = System.Runtime.InteropServices.Marshal.GetActiveObject("SldWorks.Application"); }
                catch (System.Runtime.InteropServices.COMException)
                {
                    try { com = System.Activator.CreateInstance(System.Type.GetTypeFromProgID("SldWorks.Application.34", true)); }
                    catch (System.Runtime.InteropServices.COMException)
                    { com = System.Activator.CreateInstance(System.Type.GetTypeFromProgID("SldWorks.Application", true)); }
                }
            }
            app = (SolidWorks.Interop.sldworks.ISldWorks)com;
            string revision = app.RevisionNumber();
            if (!revision.StartsWith("34.", System.StringComparison.Ordinal))
                throw new System.InvalidOperationException("SolidWorks 2026 is required.");
            report["revision"] = revision;
            original = app.CommandInProgress; captured = true;
            report["command_in_progress_original"] = original;
            app.CommandInProgress = true;
            report["command_in_progress_during"] = app.CommandInProgress;
            report["cad_invocation_started"] = true;
            report["result"] = SevenVariableAdapter.ApplyAndMeasure(root, folder, json);
        }
        catch (System.Exception ex) { report["error"] = ex.ToString(); }
        finally
        {
            if (captured)
            {
                try
                {
                    app.CommandInProgress = original;
                    report["command_in_progress_restored"] = app.CommandInProgress == original;
                }
                catch (System.Exception ex) { report["command_state_restore_error"] = ex.ToString(); }
            }
        }
        return report;
    }
}
'@
    Add-Type -TypeDefinition ([IO.File]::ReadAllText($sourcePath) + [Environment]::NewLine + [IO.File]::ReadAllText($repairSourcePath) + [Environment]::NewLine + $bridgeSource) -ReferencedAssemblies @($Interop, 'System.Web.Extensions', 'System.Core', 'Microsoft.CSharp')
    if ($CompileOnly) {
        $report.status = 'compiled_only'
        $report.finished_utc = [DateTime]::UtcNow.ToString('o')
        $report | ConvertTo-Json -Depth 10
        return
    }
    $null = Assert-SafePath $trialsParent $Root
    if (-not (Test-Path -LiteralPath $trialsParent)) { New-Item -ItemType Directory -Path $trialsParent | Out-Null }
    $null = Assert-SafePath $runFolder $trialsParent
    New-Item -ItemType Directory -Path $runFolder | Out-Null
    $ownsRunFolder = $true
    $report.status = 'cloning'
    Write-JsonReport (Join-Path $runFolder 'mapping_result.json') $report
    Write-JsonReport (Join-Path $runFolder 'design_input.json') $design
    foreach ($file in $sourceFiles) {
        $null = Assert-SafePath $file.FullName $template $true
        $destination = Assert-SafePath (Join-Path $runFolder $file.Name) $runFolder
        [IO.File]::Copy($file.FullName, $destination, $false)
    }
    $clones = @(Get-ChildItem -LiteralPath $runFolder -File | Where-Object { $_.Extension -match '^\.SLD' })
    $cloneBefore = Get-Hashes $clones
    Assert-HashesEqual $templateBefore $cloneBefore 'Cloned CAD'
    $report.clone_hashes_before = $cloneBefore
    if ($RepairOnlyDiagnostic) { throw 'RepairOnlyDiagnostic is not supported by the four-cap v6 pipeline.' }
    $report.fixed45_repair = [ordered]@{ skipped = $true; reason = 'The validated v6 template already owns the 45-degree assembly relationships. Core parts are parameterized before the assembly and four caps are loaded.' }
    $report.status = 'cad_running'
    Write-JsonReport (Join-Path $runFolder 'mapping_result.json') $report
    $bridge = [SingleDesignBridge]::Invoke($Root, $runFolder, $normalizedJson)
    $report.cad_executed = [bool]$bridge['cad_invocation_started']
    $report.execution = $bridge
    if ($bridge.ContainsKey('error')) { throw ('CAD invocation failed: ' + $bridge['error']) }
    if ($bridge.ContainsKey('command_state_restore_error') -or $bridge['command_in_progress_restored'] -ne $true) {
        throw 'SolidWorks CommandInProgress was not restored.'
    }
    $result = $bridge['result']
    if ($null -eq $result -or $result.ContainsKey('error') -or $result['completed'] -ne $true -or $result['physical_mapping_verified'] -ne $true) {
        throw 'The adapter did not pass all CAD geometry and reopened-document checks.'
    }
    $reportedCad = @($result['cad_hashes_after'])
    if ($reportedCad.Count -ne 7) { throw 'The capless adapter must return one assembly and six core-part hashes.' }
    $reportedHashes = [ordered]@{}
    foreach ($row in $reportedCad) {
        if ($reportedHashes.Contains($row.file) -or $row.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid or duplicate final CAD hash record.' }
        $reportedHashes[$row.file] = $row.sha256
    }
    $clones = @(Get-ChildItem -LiteralPath $runFolder -File | Where-Object { $_.Extension -match '^\.SLD' })
    foreach ($file in $clones) { $null = Assert-SafePath $file.FullName $runFolder $true }
    $cloneAfter = Get-Hashes $clones
    Assert-HashesEqual $reportedHashes $cloneAfter 'Saved CAD versus adapter report'
    $expectedCapless = @($pinned.Keys | Where-Object { $_ -notmatch '^封盖[1-4]\.SLDPRT$' })
    if ($expectedCapless.Count -ne 7 -or @($expectedCapless | Where-Object { -not $cloneAfter.Contains($_) }).Count -ne 0) {
        throw 'Saved CAD filenames differ from the expected capless V6 output.'
    }
    if (@($cloneAfter.Keys | Where-Object { $_ -match '^封盖[1-4]\.SLDPRT$' }).Count -ne 0) {
        throw 'A cap file remained in the parameterized output.'
    }
    $report.clone_hashes_after = $cloneAfter
    $report.status = 'geometry_checks_passed'
}
catch {
    $exitCode = 1
    $report.status = 'execution_failed'
    $report.error = $_.Exception.ToString()
}
finally {
    if ($ownsRunFolder -and $null -ne $templateBefore) {
        try {
            foreach ($file in $sourceFiles) { $null = Assert-SafePath $file.FullName $template $true }
            $report.template_hashes_after = Get-Hashes $sourceFiles
            Assert-HashesEqual $templateBefore $report.template_hashes_after 'Source template after execution'
            $report.template_unchanged = $true
        } catch {
            $exitCode = 1
            $report.status = 'execution_failed'
            $report.template_unchanged = $false
            $report.template_verification_error = $_.Exception.ToString()
        }
    }
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $report.temporary_environment_restored = ($env:TEMP -ceq $previousTemp -and $env:TMP -ceq $previousTmp)
    if (-not $report.temporary_environment_restored) {
        $exitCode = 1
        $report.status = 'execution_failed'
        $report.temporary_environment_restore_error = 'TEMP/TMP did not match their original values.'
    }
    if ($report.status -eq 'geometry_checks_passed' -and $report.temporary_environment_restored) {
        $report.status = 'geometry_verified'
        $report.completed = $true
        $report.physical_mapping_verified = $true
    }
    $report.finished_utc = [DateTime]::UtcNow.ToString('o')
    if ($ownsRunFolder) {
        try {
            $null = Assert-SafePath $runFolder $trialsParent $true
            Write-JsonReport (Join-Path $runFolder 'mapping_result.json') $report
        } catch {
            $exitCode = 1
            $report.status = 'execution_failed'
            $report.completed = $false
            $report.physical_mapping_verified = $false
            $report.report_write_error = $_.Exception.ToString()
        }
    }
}
[ordered]@{
    status = $report.status
    completed = $report.completed
    physical_mapping_verified = $report.physical_mapping_verified
    cad_executed = $report.cad_executed
    training_ready = $false
    folder = $runFolder
    report = $(if ($ownsRunFolder) { Join-Path $runFolder 'mapping_result.json' } else { $null })
    error = $(if ($report.Contains('error')) { $report.error } else { $null })
} | ConvertTo-Json -Depth 5
if ($exitCode -ne 0) { exit $exitCode }
