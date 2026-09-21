param([string]$Root = "", [switch]$LoadInstalledSimulation)
$ErrorActionPreference='Stop'
if ([string]::IsNullOrWhiteSpace($Root)) { $Root=Split-Path $PSScriptRoot -Parent }
$Root=[IO.Path]::GetFullPath($Root)
$expected=[IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
if ($Root -ne $expected) { throw 'Root must match this script task folder.' }
$env:TEMP=Join-Path $Root 'artifacts\compile_temp'
$env:TMP=$env:TEMP
[void][IO.Directory]::CreateDirectory($env:TEMP)
$sw='D:\solidworks2026\SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
$cw='D:\solidworks2026\SW\SOLIDWORKS\api\redist\SolidWorks.Interop.cosworks.dll'
[Reflection.Assembly]::LoadFrom($sw)|Out-Null
[Reflection.Assembly]::LoadFrom($cw)|Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'ContactApiProbe.cs') -ReferencedAssemblies @($sw,$cw)
$result=[ContactApiProbe]::Run($Root, [bool]$LoadInstalledSimulation)
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Root 'artifacts\contact_api_probe.json') -Encoding UTF8
[ordered]@{stage=$result.stage; revision=$result.revision; documents=$result.documents.Count; errors=$result.errors} | ConvertTo-Json -Depth 5
