param([string]$Root = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
$report=[ordered]@{timestamp=[DateTime]::UtcNow.ToString('o');stage='connect';application=$null;documents=@();errors=@()}
$out=Join-Path $Root 'artifacts\cad_inventory.json'
function Save-Report { $report | ConvertTo-Json -Depth 18 | Set-Content -LiteralPath $out -Encoding UTF8 }
function Inventory-Document($doc) {
    $entry=[ordered]@{title=$doc.GetTitle();path=$doc.GetPathName();configurations=@();active_config=$null;equations=@();features=@();errors=@()}
    try {$entry.configurations=@($doc.GetConfigurationNames());$entry.active_config=$doc.ConfigurationManager.ActiveConfiguration.Name} catch {$entry.errors += 'config: '+$_.Exception.Message}
    try {
        $eq=$doc.GetEquationMgr()
        if($null -ne $eq) {for($i=0;$i -lt $eq.GetCount();$i++){try {$entry.equations += [ordered]@{index=$i;equation=$eq.Equation($i);is_global=$eq.GlobalVariable($i)}} catch {$entry.errors += 'equation '+$i+': '+$_.Exception.Message}}}
    } catch {$entry.errors += 'eq manager: '+$_.Exception.Message}
    try {
        $feat=$doc.FirstFeature();$seen=0
        while(($null -ne $feat) -and ($seen -lt 800)) {
            $fr=[ordered]@{name=$feat.Name;type=$feat.GetTypeName2();dimensions=@()}
            try {
                $disp=$feat.GetFirstDisplayDimension();$n=0
                while(($null -ne $disp) -and ($n -lt 120)) {
                    $dim=$disp.GetDimension2(0)
                    if($null -ne $dim){$fr.dimensions += [ordered]@{full_name=$dim.FullName;value_SI=$dim.SystemValue;readonly=$dim.ReadOnly}}
                    $disp=$feat.GetNextDisplayDimension($disp);$n++
                }
            }catch {$fr.dimension_error=$_.Exception.Message}
            $entry.features += $fr
            $feat=$feat.GetNextFeature();$seen++
        }
    }catch {$entry.errors += 'feature traversal: '+$_.Exception.Message}
    return $entry
}
Save-Report
try {
    $app=New-Object -ComObject 'SldWorks.Application.34'
    $report.application=[ordered]@{revision=$app.RevisionNumber();prog_id='SldWorks.Application.34'}
    Save-Report
    if(-not $report.application.revision.StartsWith('34.')){throw 'Unexpected CAD major version; stopped'}
    $report.stage='open_readonly'
    Save-Report
    $base=Join-Path $Root 'working\baseline'
    $cadFiles=@(Get-ChildItem -LiteralPath $base -File | Where-Object {$_.Extension -ieq '.SLDPRT'})
    $cadFiles+=@(Get-ChildItem -LiteralPath $base -File | Where-Object {$_.Extension -ieq '.SLDASM'})
    foreach($f in $cadFiles) {
        $type=1;if($f.Extension -ieq '.SLDASM'){$type=2}
        $openErrors=0;$openWarnings=0
        $doc=$app.OpenDoc6($f.FullName,$type,3,'',[ref]$openErrors,[ref]$openWarnings)
        if($null -eq $doc){$report.errors+=[ordered]@{file=$f.Name;open_errors=$openErrors;open_warnings=$openWarnings};Save-Report;continue}
        $entry=Inventory-Document $doc
        $entry.open_errors=$openErrors;$entry.open_warnings=$openWarnings
        if($type -eq 2) {
            try {
                $entry.components=@()
                foreach($component in @($doc.GetComponents($false))) {
                    $entry.components += [ordered]@{name=$component.Name2;path=$component.GetPathName();configuration=$component.ReferencedConfiguration;suppression=$component.GetSuppression()}
                }
            }catch {$entry.errors+='components: '+$_.Exception.Message}
        }
        $report.documents+=$entry
        Save-Report
    }
    $report.stage='inventory_finished'
} catch {$report.errors += $_.Exception.Message;$report.stage='failed'}
Save-Report
[ordered]@{stage=$report.stage;revision=$report.application;documents=$report.documents.Count;errors=$report.errors;output=$out}|ConvertTo-Json -Depth 6
