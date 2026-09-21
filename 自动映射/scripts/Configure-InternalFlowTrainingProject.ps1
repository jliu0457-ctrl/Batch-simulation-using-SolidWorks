param(
    [int]$SolidWorksPid = 0,
    [string]$ProjectName = '流体力学仿真'
)

$ErrorActionPreference = 'Stop'
$templatePath = 'C:\ProgramData\COSMOS Applications\Flow Simulation 2026\template\internal_water.fwp'
if (-not (Test-Path -LiteralPath $templatePath)) { throw "Missing Flow Simulation template: $templatePath" }

function Set-FeatureParameter($Feature, [int]$Type, $Value, [switch]$Long) {
    $parameterized = $Feature.GetInterface('IParametrizedFeature')
    if (-not $parameterized) { throw "Feature does not expose IParametrizedFeature (parameter $Type)." }
    $parameters = $parameterized.EnumParameters()
    if (-not $parameters) { throw "Feature has no parameter enumeration (parameter $Type)." }
    $parameters.Reset()
    while ($parameter = $parameters.Next()) {
        if ([int]$parameter.Type -eq $Type) {
            if ($Long) { $result = $parameter.SetLongValue([int]$Value) }
            else { $result = $parameter.SetValue([double]$Value) }
            return [pscustomobject]@{ type = $Type; value = $Value; result = $result }
        }
    }
    throw "Parameter type $Type was not found."
}

function Add-NamedReference($Topology, [string]$Reference) {
    if ($Reference -notmatch '@') {
        $ok = $Topology.AddComponent(0, $Reference)
        if (-not $ok) { throw "Could not bind component reference $Reference." }
        return $Reference
    }
    $parts = $Reference -split '@', 2
    if ($parts.Count -ne 2) { throw "Unsupported named-face reference: $Reference" }
    $faceName = $parts[0]
    $componentName = $parts[1]
    $ok = $Topology.AddFaces(0, $componentName, '', $true, $faceName, $false, 0, 0, 0)
    if (-not $ok) { throw "Could not bind reference $Reference." }
    return $Reference
}

function Add-BoundaryCondition($Project, [string]$Name, [int]$ConditionType, [int]$ValueType, [double]$Value, [string]$Reference) {
    $feature = $Project.CreateTemporaryFeature(2)
    $typeResult = Set-FeatureParameter $feature 41 $ConditionType -Long
    $valueResult = Set-FeatureParameter $feature $ValueType $Value
    $topology = $feature.GetInterface('ITopologyBasedFeature')
    $boundReference = Add-NamedReference $topology $Reference
    $nameResult = $feature.SetName($Name)
    $addResult = $Project.AddTemporaryFeature($feature)
    if (-not $addResult) { throw "Failed to add boundary condition $Name." }
    return [pscustomobject]@{
        name = $Name; type = $ConditionType; value_parameter = $ValueType; value = $Value
        reference = $boundReference; set_name_result = $nameResult
        type_result = $typeResult.result; value_result = $valueResult.result; added = $addResult
    }
}

function Add-SurfaceGoal($Project, [string]$Name, [int]$GoalParameter, $CalculationType, [string[]]$References) {
    $feature = $Project.CreateTemporaryFeature(12)
    $goal = $feature.GetInterface('IParameterGoal')
    if (-not $goal) { throw "Surface goal $Name does not expose IParameterGoal." }
    $setParameterResult = $goal.SetParameter($GoalParameter)
    $setCalculationResult = $null
    if ($null -ne $CalculationType) { $setCalculationResult = $goal.SetValueToCalculate([int]$CalculationType) }
    $topology = $feature.GetInterface('ITopologyBasedFeature')
    $boundReferences = @()
    foreach ($reference in $References) { $boundReferences += Add-NamedReference $topology $reference }
    $nameResult = $feature.SetName($Name)
    $addResult = $Project.AddTemporaryFeature($feature)
    if (-not $addResult) { throw "Failed to add surface goal $Name." }
    return [pscustomobject]@{
        name = $Name; parameter = $GoalParameter; calculation = $CalculationType
        references = $boundReferences; set_name_result = $nameResult
        set_parameter_result = $setParameterResult; set_calculation_result = $setCalculationResult
        parameter_readback = $goal.Parameter; calculation_readback = $goal.ValueToCalculate; added = $addResult
    }
}

$nca = New-Object -ComObject NIKCommonApi2.BaseApiObject
if (-not $nca.LoadProductAPI2('Flow Simulation', '2026')) { throw 'Cannot load Flow Simulation API 2026.' }
try {
    if ($SolidWorksPid -le 0) {
        $process = Get-Process SLDWORKS | Sort-Object StartTime -Descending | Select-Object -First 1
        if (-not $process) { throw 'No running SOLIDWORKS process.' }
        $SolidWorksPid = $process.Id
    }
    $application = $nca.Attach2RunningObject2($SolidWorksPid)
    if (-not $application) { throw "Cannot attach to SOLIDWORKS PID $SolidWorksPid." }
    $document = $application.ActiveDocument
    $configuration = $document.ActiveConfiguration

    $existingNames = @($configuration.GetProjectNames())
    if ($existingNames -contains $ProjectName) {
        if (-not $configuration.RemoveProject($ProjectName)) {
            throw "Project '$ProjectName' already exists and could not be removed."
        }
    }

    $project = $document.CreateProjectFromTemplate($templatePath, $ProjectName)
    if (-not $project) { throw 'CreateProjectFromTemplate returned no project.' }
    $project = $configuration.ActivateProject($ProjectName, $false)
    if (-not $project) { throw "Could not activate newly created project '$ProjectName'." }

    $boundaries = @(
        Add-BoundaryCondition $project '入口速度 2' 2 19 3.0 'FLOW_INLET_INNER@封盖2<1>'
        Add-BoundaryCondition $project '出口静压 2' 10 24 101325.0 'FLOW_OUTLET_INNER@封盖1<1>'
    )
    $goals = @(
        Add-SurfaceGoal $project 'SG CV入口静压' 3 2 @('FLOW_INLET_INNER@封盖2<1>')
        Add-SurfaceGoal $project 'SG CV出口静压' 3 2 @('FLOW_OUTLET_INNER@封盖1<1>')
        Add-SurfaceGoal $project 'SG CV入口端面体积流量' 17 16 @('FLOW_INLET_INNER@封盖2<1>')
        Add-SurfaceGoal $project 'SG 蝶板法向压力' 3 1 @('面<1>@8“D94R3Y-CL600C-11蝶板<1>')
        Add-SurfaceGoal $project 'SG 密比压 平均' 3 2 @(
            '面<2>@8“D94R3Y-CL600C-08大垫片<1>',
            '面<3>@8“D94R3Y-CL600C-08大垫片<1>',
            '面<1>@8“D94R3Y-CL600C-11蝶板<1>',
            '面<4>@8“D94R3Y-CL600C-09密封圈<1>'
        )
        Add-SurfaceGoal $project 'SG 密比压 最大' 3 1 @(
            '面<2>@8“D94R3Y-CL600C-08大垫片<1>',
            '面<3>@8“D94R3Y-CL600C-08大垫片<1>',
            '面<1>@8“D94R3Y-CL600C-11蝶板<1>',
            '面<4>@8“D94R3Y-CL600C-09密封圈<1>'
        )
        Add-SurfaceGoal $project 'SG 力矩Z' 24 23 @(
            '面<1>@8“D94R3Y-CL600C-04阀轴<1>',
            '8“D94R3Y-CL600C-11蝶板<1>'
        )
    )

    $meshFeature = $project.GlobalMeshSettings.MeshSettings
    $meshParameter = $meshFeature.GetInterface('IParametrizedFeature')
    $meshParameters = $meshParameter.EnumParameters(); $meshParameters.Reset()
    $meshLevelResult = $null
    while ($parameter = $meshParameters.Next()) {
        if ([int]$parameter.Type -eq 7) { $meshLevelResult = $parameter.SetLongValue(7); break }
    }
    if ($null -eq $meshLevelResult) { throw 'Could not find the initial mesh level parameter.' }

    $control = $project.GetCalculationControlOptions()
    $control.UseGoalsConvergence = $true
    $control.UseMaximumTravels = $true
    $control.MaximumTravels = 12
    $control.UseMaximumIterations = $true
    $control.MaximumIterations = 500

    $rebuildResult = $document.Rebuild()
    $updateResult = $project.UpdateConfigAndDataFiles()
    $saveResult = $document.Save()
    $activeProject = $configuration.ActivateProject($ProjectName, $false)

    $features = @()
    $featureEnum = $activeProject.EnumFeatures(); $featureEnum.Reset()
    while ($feature = $featureEnum.Next()) {
        $refs = @()
        try {
            $topology = $feature.GetInterface('ITopologyBasedFeature')
            if ($topology) { $refs = @($topology.GetReferencesNames()) }
        } catch {}
        $features += [pscustomobject]@{ name = $feature.Name; type = [int]$feature.Type; references = $refs }
    }

    $projectDirectory = $activeProject.ProjectFiles.ProjectDirectory
    $xmlConfig = Get-ChildItem -LiteralPath $projectDirectory -Filter '*.xmlconfig' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    $flowSpaceType = $null
    if ($xmlConfig) {
        $xmlText = Get-Content -LiteralPath $xmlConfig.FullName -Raw
        $match = [regex]::Match($xmlText, '<FlowSpaceType value="([0-9]+)"')
        if ($match.Success) { $flowSpaceType = [int]$match.Groups[1].Value }
    }

    [pscustomobject]@{
        pid = $SolidWorksPid
        document = $document.Name
        configuration = $configuration.Name
        project = $activeProject.Name
        project_directory = $projectDirectory
        xmlconfig = if ($xmlConfig) { $xmlConfig.FullName } else { $null }
        flow_space_type = $flowSpaceType
        internal_flow_validated = ($flowSpaceType -eq 1)
        boundaries = $boundaries
        goals = $goals
        mesh_level = 7
        maximum_travels = $control.MaximumTravels
        maximum_iterations = $control.MaximumIterations
        rebuild_result = $rebuildResult
        update_result = $updateResult
        save_result = $saveResult
        features = $features
    } | ConvertTo-Json -Depth 8
} finally {
    try { $nca.UnloadProductAPI() } catch {}
}
