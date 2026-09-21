param([int]$SolidWorksPid = 0)

$ErrorActionPreference = 'Stop'
$nca = New-Object -ComObject NIKCommonApi2.BaseApiObject
if (-not $nca.LoadProductAPI2('Flow Simulation', '2026')) { throw 'Cannot load Flow Simulation API 2026.' }
if ($SolidWorksPid -le 0) {
    $SolidWorksPid = (Get-Process SLDWORKS | Sort-Object StartTime -Descending | Select-Object -First 1).Id
}
$ia = $nca.Attach2RunningObject2($SolidWorksPid)
$project = $ia.ActiveDocument.ActiveConfiguration.GetFluidDynamicAnalysisProject()
$rows = @()
foreach ($spec in @(
    @{ parameter = 3; calculation = 2 },
    @{ parameter = 17; calculation = 2 },
    @{ parameter = 17; calculation = 3 }
)) {
    $feature = $project.CreateTemporaryFeature(12)
    $goal = $feature.GetInterface('IParameterGoal')
    $row = [ordered]@{ parameter = $spec.parameter; calculation = $spec.calculation }
    try { $row.set_parameter_result = $goal.SetParameter($spec.parameter) } catch { $row.set_parameter_error = $_.Exception.Message }
    try { $row.set_calculation_result = $goal.SetValueToCalculate($spec.calculation) } catch { $row.set_calculation_error = $_.Exception.Message }
    try { $row.parameter_readback = $goal.Parameter } catch {}
    try { $row.calculation_readback = $goal.ValueToCalculate } catch {}
    $rows += [pscustomobject]$row
}
$nca.UnloadProductAPI()
$rows | ConvertTo-Json -Depth 4
