param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$env:TEMP=Join-Path $Root 'artifacts\compile_temp'
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$interop='D:\solidworks2026\SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
[Reflection.Assembly]::LoadFrom($interop)|Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'GeometryInspector.cs') -ReferencedAssemblies $interop
$r=[GeometryInspector]::Run($Root)
$r | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath (Join-Path $Root 'artifacts\geometry_inventory.json') -Encoding UTF8
[ordered]@{status=$r.status;revision=$r.revision;documents=$r.documents.Count}|ConvertTo-Json
