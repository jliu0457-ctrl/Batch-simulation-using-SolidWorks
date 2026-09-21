<#
.SYNOPSIS
Freeze the saved, sealed-flow SolidWorks assembly as the immutable v5 mapping template.
.DESCRIPTION
Run only after saving and closing SolidWorks. The source must contain exactly one
assembly and fourteen external parts, including fixed endcaps 封盖1 through 封盖8.
The script never overwrites an existing v5 template or manifest.
#>
[CmdletBinding()]
param([string]$Source = '')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$taskRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $Source) { $Source = $taskRoot }
$sourceRoot = [IO.Path]::GetFullPath($Source).TrimEnd('\', '/')
if ($sourceRoot -cne [IO.Path]::GetFullPath($taskRoot).TrimEnd('\', '/')) {
    throw 'The v5 source must be the current task workspace root.'
}

$mappingRoot = Split-Path $PSScriptRoot -Parent
$template = Join-Path $mappingRoot 'working\assembly_repair_v5'
$manifestPath = Join-Path $mappingRoot 'config\cad_template_manifest_v5.json'
if (Test-Path -LiteralPath $template) { throw "Refusing to overwrite existing template: $template" }
if (Test-Path -LiteralPath $manifestPath) { throw "Refusing to overwrite existing manifest: $manifestPath" }

$files = @(Get-ChildItem -LiteralPath $sourceRoot -File | Where-Object { $_.Extension -match '^\.SLD(ASM|PRT)$' } | Sort-Object Name)
$assemblies = @($files | Where-Object Extension -ieq '.SLDASM')
$parts = @($files | Where-Object Extension -ieq '.SLDPRT')
if ($assemblies.Count -ne 1 -or $parts.Count -ne 14 -or $files.Count -ne 15) {
    throw 'Sealed-flow v5 requires exactly one assembly and fourteen external parts.'
}
$capPrefix = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5bCB55uW'))
$requiredCaps = @(1..8 | ForEach-Object { "$capPrefix$_.SLDPRT" })
$partNames = @($parts.Name)
foreach ($cap in $requiredCaps) {
    if ($partNames -cnotcontains $cap) { throw "Missing fixed sealing part: $cap" }
}

New-Item -ItemType Directory -Path $template | Out-Null
$hashes = [ordered]@{}
foreach ($file in $files) {
    $destination = Join-Path $template $file.Name
    [IO.File]::Copy($file.FullName, $destination, $false)
    $hashes[$file.Name] = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
}

$manifest = [ordered]@{
    template_id = 'sealed_flow_v5'
    created_utc = [DateTime]::UtcNow.ToString('o')
    source = $sourceRoot
    expected_assemblies = 1
    expected_external_parts = 14
    fixed_opening_deg = 45
    fixed_endcap_parts = $requiredCaps
    files = $hashes
}
$json = ConvertTo-Json -InputObject $manifest -Depth 5
[IO.File]::WriteAllText($manifestPath, $json, [Text.UTF8Encoding]::new($false))
$manifest
