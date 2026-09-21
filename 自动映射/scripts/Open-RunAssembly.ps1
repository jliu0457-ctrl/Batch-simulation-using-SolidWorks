param([Parameter(Mandatory=$true)][string]$RunFolder)
$ErrorActionPreference='Stop'
$folder=[IO.Path]::GetFullPath($RunFolder).TrimEnd('\')
$allowed=[IO.Path]::GetFullPath((Join-Path (Split-Path (Split-Path $PSCommandPath -Parent) -Parent) 'working\seven_variable_trials')).TrimEnd('\')
if(-not $folder.StartsWith($allowed+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Run folder is outside seven_variable_trials.'}
$assembly=@(Get-ChildItem -LiteralPath $folder -Filter '*.SLDASM' -File)
if($assembly.Count -ne 1){throw "Expected exactly one assembly; found $($assembly.Count)."}
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
[Reflection.Assembly]::LoadFrom($interop)|Out-Null
$source=@'
using System;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;
public static class OpenRunAssembly {
 public static object Run(string path){object com;try{com=Marshal.GetActiveObject("SldWorks.Application.34");}catch{com=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true));}var app=(SW.ISldWorks)com;app.Visible=true;int e=0,w=0;var d=(SW.IModelDoc2)app.OpenDoc6(path,2,1,"开度45°",ref e,ref w);if(d==null)throw new Exception("OpenDoc6 failed: "+e+","+w);int a=0;app.ActivateDoc3(d.GetTitle(),false,0,ref a);return new{path=d.GetPathName(),title=d.GetTitle(),open_errors=e,open_warnings=w,activate_errors=a,configuration=d.ConfigurationManager.ActiveConfiguration.Name};}
}
'@
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop,'System.Core')
[OpenRunAssembly]::Run($assembly[0].FullName)|ConvertTo-Json -Compress
