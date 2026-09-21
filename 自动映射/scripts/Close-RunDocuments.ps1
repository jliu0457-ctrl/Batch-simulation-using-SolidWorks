param([Parameter(Mandatory = $true)][string]$RunFolder)
$ErrorActionPreference = 'Stop'
$mappingRoot = Split-Path (Split-Path $PSCommandPath -Parent) -Parent
$runsRoot = [IO.Path]::GetFullPath((Join-Path $mappingRoot 'working\seven_variable_trials')).TrimEnd('\')
$folder = [IO.Path]::GetFullPath($RunFolder).TrimEnd('\')
if (-not $folder.StartsWith($runsRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'RunFolder must be inside working\seven_variable_trials.'
}
$interop = 'D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
$source = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;
public static class TargetedDocumentCloser {
  public static string[] Close(string folder) {
    object com = Marshal.GetActiveObject("SldWorks.Application.34");
    var app = (SW.ISldWorks)com;
    string prefix = System.IO.Path.GetFullPath(folder).TrimEnd('\\') + "\\";
    var titles = new List<string>();
    var doc = (SW.IModelDoc2)app.GetFirstDocument();
    int guard = 0;
    while (doc != null && guard++ < 1000) {
      string path = doc.GetPathName();
      if (!String.IsNullOrEmpty(path) && System.IO.Path.GetFullPath(path).StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        titles.Add(doc.GetTitle());
      doc = (SW.IModelDoc2)doc.GetNext();
    }
    foreach (string title in titles) app.CloseDoc(title);
    return titles.ToArray();
  }
}
'@
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop, 'System.Core')
[TargetedDocumentCloser]::Close($folder) | ConvertTo-Json
