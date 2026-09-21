param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$env:TEMP=Join-Path $Root 'artifacts\compile_temp'
$env:TMP=$env:TEMP
$interop='D:\solidworks2026\SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
[Reflection.Assembly]::LoadFrom($interop)|Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'RepairAssembly.cs') -ReferencedAssemblies @($interop,'System.Core')
$r=[Fixed45.RepairAssembly]::Run($Root)
$r|ConvertTo-Json -Depth 30|Set-Content -LiteralPath (Join-Path $Root 'artifacts\assembly_repair.json') -Encoding UTF8
$r|Select-Object status,mate_created,mate_error,final_rebuild_ok,save_ok,error|ConvertTo-Json -Depth 4
