param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$env:TEMP=Join-Path $Root 'artifacts\compile_temp'
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force|Out-Null
$interop='D:\solidworks2026\SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
[Reflection.Assembly]::LoadFrom($interop)|Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'CadExperiments.cs') -ReferencedAssemblies @($interop,'System.Web.Extensions','System.Core')
$r=[CadExperiments]::Run($Root,(Join-Path $Root 'config\candidate_trials.json'))
$r|ConvertTo-Json -Depth 40|Set-Content -LiteralPath (Join-Path $Root 'artifacts\candidate_trials.json') -Encoding UTF8
[ordered]@{status=$r.status;count=$r.results.Count;passed=@($r.results|Where-Object cad_edit_rebuild_passed -eq $true).Count;purpose=$r.purpose}|ConvertTo-Json
