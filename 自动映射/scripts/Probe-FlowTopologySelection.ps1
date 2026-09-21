param([int]$SolidWorksPid = 0)

$ErrorActionPreference = 'Stop'
$nca = New-Object -ComObject NIKCommonApi2.BaseApiObject
if (-not $nca.LoadProductAPI2('Flow Simulation', '2026')) { throw 'Cannot load Flow Simulation API 2026.' }
try {
    if ($SolidWorksPid -le 0) { $SolidWorksPid = (Get-Process SLDWORKS | Sort-Object StartTime -Descending | Select-Object -First 1).Id }
    $application = $nca.Attach2RunningObject2($SolidWorksPid)
    $project = $application.ActiveDocument.ActiveConfiguration.GetFluidDynamicAnalysisProject()
    $sw = [Runtime.InteropServices.Marshal]::GetActiveObject('SldWorks.Application.34')
    $model = $sw.IActiveDoc2
    if (-not $model) { $model = $sw.ActiveDoc }
    if (-not $model) { throw 'The SolidWorks native API returned no active document.' }
    $rows = @()
    foreach ($reference in @('面<1>@封盖1<1>', '面<1>@封盖2<1>')) {
        $model.ClearSelection2($true)
        $selected = $model.Extension.SelectByID2($reference, 'FACE', 0, 0, 0, $false, 0, $null, 0)
        $feature = $project.CreateTemporaryFeature(2)
        $topology = $feature.GetInterface('ITopologyBasedFeature')
        $updated = $false
        $names = @()
        if ($selected) {
            $updated = $topology.UpdateReferenciesFromSelection()
            if ($updated) { $names = @($topology.GetReferencesNames()) }
        }
        $rows += [pscustomobject]@{ reference = $reference; selected = $selected; updated = $updated; readback = $names }
    }
    $model.ClearSelection2($true)
    $rows | ConvertTo-Json -Depth 5
} finally {
    try { $nca.UnloadProductAPI() } catch {}
}
