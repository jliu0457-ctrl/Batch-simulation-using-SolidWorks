param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$env:TEMP=Join-Path $Root 'artifacts\compile_temp'
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$interop='D:\solidworks2026\SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
[Reflection.Assembly]::LoadFrom($interop)|Out-Null
try {
 Add-Type -Path (Join-Path $PSScriptRoot 'CadInspector.cs') -ReferencedAssemblies $interop
 $result=[CadInspector]::Run($Root)
 $result | ConvertTo-Json -Depth 25 | Set-Content -LiteralPath (Join-Path $Root 'artifacts\cad_inventory_typed.json') -Encoding UTF8
 [ordered]@{stage=$result.stage;revision=$result.revision;documents=$result.documents.Count;errors=$result.errors}|ConvertTo-Json -Depth 5
} catch { $_.Exception.ToString() | Set-Content -LiteralPath (Join-Path $Root 'artifacts\cad_typed_error.txt') -Encoding UTF8; throw }
