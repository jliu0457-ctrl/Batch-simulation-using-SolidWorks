[CmdletBinding()]
param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$compileTemp=Join-Path $Root 'artifacts\compile_temp'
$env:TEMP=$compileTemp
$env:TMP=$compileTemp
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
[Reflection.Assembly]::LoadFrom($interop)|Out-Null
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'RepairAssembly.cs'))) -ReferencedAssemblies @($interop,'System.Core')
$result=[Fixed45.RepairAssembly]::RunFolder($Root,'assembly_repair_v5',$false)
$artifact=Join-Path $Root 'artifacts\assembly_repair_v5.json'
$result|ConvertTo-Json -Depth 30|Set-Content -LiteralPath $artifact -Encoding UTF8
if($result.status -ne 'repair_trial_finished'){throw $result.error}
$manifestPath=Join-Path $Root 'config\cad_template_manifest_v5.json'
$manifest=Get-Content -Raw -LiteralPath $manifestPath|ConvertFrom-Json
$assembly=Get-ChildItem -LiteralPath (Join-Path $Root 'working\assembly_repair_v5') -Filter *.SLDASM -File|Select-Object -Single 1
$manifest.files.($assembly.Name)=(Get-FileHash -LiteralPath $assembly.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $manifestPath -Encoding UTF8
$result|Select-Object status,mate_created,mate_error,final_rebuild_ok,save_ok,reopen_axis|ConvertTo-Json -Depth 8
