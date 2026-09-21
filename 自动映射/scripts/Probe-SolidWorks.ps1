param([Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
$result = [ordered]@{checked_at=[DateTime]::UtcNow.ToString('o'); process=@(); registry=@(); connection=$null; documents=@(); errors=@()}
foreach($p in @(Get-Process -Name SLDWORKS -ErrorAction SilentlyContinue)) {
    try { $result.process += [ordered]@{id=$p.Id;path=$p.Path;file_version=$p.MainModule.FileVersionInfo.FileVersion;product_version=$p.MainModule.FileVersionInfo.ProductVersion} }
    catch { $result.errors += 'Process metadata: '+$_.Exception.Message }
}
foreach($key in @('Registry::HKEY_CLASSES_ROOT\SldWorks.Application\CLSID','Registry::HKEY_LOCAL_MACHINE\SOFTWARE\SolidWorks','Registry::HKEY_LOCAL_MACHINE\SOFTWARE\SolidWorks\Addins')) {
    try { if(Test-Path -LiteralPath $key){$result.registry += [ordered]@{key=$key;children=@(Get-ChildItem -LiteralPath $key -ErrorAction SilentlyContinue | Select-Object -ExpandProperty PSChildName)}} }
    catch {$result.errors += 'Registry: '+$_.Exception.Message}
}
try {
    $app=[Runtime.InteropServices.Marshal]::GetActiveObject('SldWorks.Application')
    $result.connection=[ordered]@{status='connected_existing';revision=$app.RevisionNumber()}
    $docs=$app.GetDocuments()
    if($null -ne $docs) {foreach($doc in $docs){$result.documents += [ordered]@{title=$doc.GetTitle();path=$doc.GetPathName();type=$doc.GetType();dirty=$doc.GetSaveFlag()}}}
} catch {$result.connection=[ordered]@{status='unavailable';error=$_.Exception.Message;hresult=$_.Exception.HResult}}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
$result | ConvertTo-Json -Depth 8
