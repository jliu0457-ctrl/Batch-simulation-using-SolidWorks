param([int]$SolidWorksPid = 0)

$ErrorActionPreference = 'Stop'

function Read-ParameterValue($Parameter) {
    $result = [ordered]@{ type = [int]$Parameter.Type }
    try {
        $result.kind = 'long'
        $result.value = $Parameter.LongValue
    } catch {
        try {
            $result.kind = 'double'
            $result.value = $Parameter.Value
        } catch {
            $result.kind = 'unreadable'
            $result.value = $null
        }
    }
    return [pscustomobject]$result
}

$nca = New-Object -ComObject NIKCommonApi2.BaseApiObject
if (-not $nca.LoadProductAPI2('Flow Simulation', '2026')) { throw 'Cannot load Flow Simulation API 2026.' }

if ($SolidWorksPid -le 0) {
    $process = Get-Process SLDWORKS | Sort-Object StartTime -Descending | Select-Object -First 1
    if (-not $process) { throw 'No running SOLIDWORKS process.' }
    $SolidWorksPid = $process.Id
}

$ia = $nca.Attach2RunningObject2($SolidWorksPid)
if (-not $ia) { throw "Cannot attach to SOLIDWORKS PID $SolidWorksPid." }
$doc = $ia.ActiveDocument
$configuration = $doc.ActiveConfiguration
$project = $configuration.GetFluidDynamicAnalysisProject()

$output = [ordered]@{
    pid = $SolidWorksPid
    document = $doc.Name
    configuration = $configuration.Name
    project = $project.Name
    existing = @()
    temporary = @()
}

$enum = $project.EnumFeatures()
$enum.Reset()
while ($feature = $enum.Next()) {
    $row = [ordered]@{ name = $feature.Name; type = [int]$feature.Type; parameters = @() }
    try {
        $pf = $feature.GetInterface('IParametrizedFeature')
        if ($pf) {
            $pe = $pf.EnumParameters()
            if ($pe) {
                $pe.Reset()
                while ($p = $pe.Next()) { $row.parameters += Read-ParameterValue $p }
            }
        }
    } catch {}
    try {
        $goal = $feature.GetInterface('IParameterGoal')
        if ($goal) {
            $row.goal_parameter = $goal.Parameter
            $row.goal_value_to_calculate = $goal.ValueToCalculate
            try {
                [long]$goalValue = -1
                $row.goal_get_value_result = $goal.GetValueToCalculate([ref]$goalValue)
                $row.goal_get_value = $goalValue
            } catch {}
        }
    } catch {}
    $output.existing += [pscustomobject]$row
}

foreach ($featureType in @(2, 12)) {
    $feature = $project.CreateTemporaryFeature($featureType)
    $row = [ordered]@{ type = $featureType; parameters = @() }
    $pf = $feature.GetInterface('IParametrizedFeature')
    if ($pf) {
        $pe = $pf.EnumParameters()
        if ($pe) {
            $pe.Reset()
            while ($p = $pe.Next()) { $row.parameters += Read-ParameterValue $p }
        }
    }
    try {
        $goal = $feature.GetInterface('IParameterGoal')
        if ($goal) {
            $row.goal_parameter = $goal.Parameter
            $row.goal_value_to_calculate = $goal.ValueToCalculate
            try {
                [long]$goalValue = -1
                $row.goal_get_value_result = $goal.GetValueToCalculate([ref]$goalValue)
                $row.goal_get_value = $goalValue
            } catch {}
        }
    } catch {}
    $output.temporary += [pscustomobject]$row
}

$nca.UnloadProductAPI()
$output | ConvertTo-Json -Depth 8
