# Read existing Flow .fld goal data through the registered official API only.
# Does not attach to SolidWorks, open a CAD model, mesh, or solve.
[CmdletBinding()]
param([string]$FldPath = "", [string]$OutputPath = "")
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\') + '\'
if ([string]::IsNullOrWhiteSpace($FldPath)) { $FldPath = Join-Path $taskRoot "source_snapshot/1/1.fld" }
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path $taskRoot "artifacts/flow_api_probe.json" }
$fldFull = [IO.Path]::GetFullPath($FldPath)
$outputFull = [IO.Path]::GetFullPath($OutputPath)
if (-not $fldFull.StartsWith($taskRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Input must be an existing fld inside this task directory.'
}
if (-not $outputFull.StartsWith($taskRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output must stay inside this task directory.'
}
if (-not (Test-Path -LiteralPath $fldFull -PathType Leaf) -or [IO.Path]::GetExtension($fldFull) -ne '.fld') {
    throw 'Existing .fld file required.'
}
$report = [ordered]@{
    schema_version = 1
    mode = 'read_only_existing_fld_api_probe'
    started_utc = [DateTime]::UtcNow.ToString('o')
    product = 'Flow Simulation'
    requested_version = '2026'
    prog_id = 'NIKCommonApi2.BaseApiObject'
    input_fld = $fldFull
    input_sha256_before = (Get-FileHash -LiteralPath $fldFull -Algorithm SHA256).Hash.ToLowerInvariant()
    attached_to_cad = $false
    cad_document_opened = $false
    meshing_started = $false
    solver_started = $false
    product_load_return = $null
    status = 'pending'
    stage = 'initialization'
    goals = @()
    errors = @()
    publishable = $false
    training_labels = @{T_peak_Nm=$null; sigma_n_MPa=$null; q_calc_MPa=$null; Cv=$null}
    limitations = @(
        'Reading old fld goals does not certify current CAD/result correspondence.',
        'No live solver or license readiness is inferred from file-reading success.',
        'No contact stress or q_calc is derived from CFD pressure goal names.'
    )
}
$nca = $null
$handler = $null
$allGoals = $null
$enumerator = $null
$goal = $null
function Get-ErrorRecordData($record) {
    $details = @()
    $ex = $record.Exception
    while ($null -ne $ex) {
        $details += [ordered]@{type=$ex.GetType().FullName; message=$ex.Message; hresult=$ex.HResult}
        $ex = $ex.InnerException
    }
    return [ordered]@{message=$record.ToString(); details=$details}
}
try {
    $report.stage = 'create_registered_base_api'
    $type = [Type]::GetTypeFromProgID($report.prog_id, $true)
    $nca = [Activator]::CreateInstance($type)
    $report.stage = 'LoadProductAPI2'
    $loadResult = $nca.LoadProductAPI2('Flow Simulation', '2026')
    $report.product_load_return = [bool]$loadResult
    if (-not $loadResult) {
        throw 'LoadProductAPI2("Flow Simulation","2026") returned FALSE.'
    }
    $report.stage = 'LoadFDAResultFile_goals_only'
    $handler = $nca.LoadFDAResultFile($fldFull, $true)
    if ($null -eq $handler) { throw 'LoadFDAResultFile returned null.' }
    $report.stage = 'GetGoalsCalculationResults2'
    $allGoals = $handler.GetGoalsCalculationResults2()
    if ($null -eq $allGoals) { throw 'GetGoalsCalculationResults2 returned null.' }
    $enumerator = $allGoals.GetGoalsEnum()
    $enumerator.Reset()
    $goalRecords = New-Object 'System.Collections.Generic.List[object]'
    $report.stage = 'enumerate_goal_values'
    for ($i = 0; $i -lt 1000; $i++) {
        $goal = $enumerator.Next()
        if ($null -eq $goal) { break }
        $name = [string]$goal.GetGoalName()
        $last = [double]$goal.GetLastCalculatedValue()
        $history = @($goal.GetValues2())
        $finite = -not ([double]::IsNaN($last) -or [double]::IsInfinity($last))
        $item = [ordered]@{
            name = $name
            last_value = $(if ($finite) { $last } else { $last.ToString() })
            last_is_finite = $finite
            values_si = $history
            history_count = $history.Count
            unit_system = 'SI per official GetValues2 documentation'
            source_fld_sha256 = $report.input_sha256_before
            training_label = $null
        }
        $goalRecords.Add($item)
        if ([Runtime.InteropServices.Marshal]::IsComObject($goal)) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($goal)
        }
        $goal = $null
    }
    if ($i -ge 1000) { throw 'Goal enumeration exceeded diagnostic limit.' }
    $report.goals = $goalRecords.ToArray()
    $report.status = 'existing_fld_goals_read'
    $report.stage = 'completed_read_only'
}
catch {
    $report.status = 'api_probe_failed'
    $report.errors += Get-ErrorRecordData $_
}
finally {
    foreach ($obj in @($goal, $enumerator, $allGoals, $handler)) {
        if ($null -ne $obj -and [Runtime.InteropServices.Marshal]::IsComObject($obj)) {
            try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($obj) } catch {}
        }
    }
    if ($null -ne $nca) {
        if ($report.product_load_return -eq $true) {
            try { $nca.UnloadProductAPI() } catch { $report.errors += Get-ErrorRecordData $_ }
        }
        if ([Runtime.InteropServices.Marshal]::IsComObject($nca)) {
            try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($nca) } catch {}
        }
    }
    $report.input_sha256_after = (Get-FileHash -LiteralPath $fldFull -Algorithm SHA256).Hash.ToLowerInvariant()
    $report.input_unchanged = $report.input_sha256_before -eq $report.input_sha256_after
    $report.ended_utc = [DateTime]::UtcNow.ToString('o')
    $parent = Split-Path $outputFull -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void][IO.Directory]::CreateDirectory($parent)
    }
    [IO.File]::WriteAllText($outputFull, ($report | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))
}
[pscustomobject]@{status=$report.status; stage=$report.stage; product_load=$report.product_load_return;
    goal_count=$report.goals.Count; input_unchanged=$report.input_unchanged; report=$outputFull} | ConvertTo-Json
