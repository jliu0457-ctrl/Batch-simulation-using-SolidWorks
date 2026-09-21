param(
    [Parameter(Mandatory=$true)][string]$DocPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$ConfigName = ''
)
# Read-only listing of a document's equation manager: every equation string, its value
# and its status, plus the angular-equation-unit setting.  Nothing is written.
# ASCII only; Chinese uses unicode escapes in the C# source.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "SolidWorks interop missing: $interop" }
$source=@'
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class ProbeEquations {
 static string S(Func<string> f){try{return f();}catch{return "";}}
 public static object Run(string docPath,string configName){
  docPath=Path.GetFullPath(docPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  // 2 = assembly; type 1 = part.  Read-only, silent.
  // configName matters: equations belong to a configuration, so opening the wrong one
  // hides them.
  int errs=0,warns=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(docPath,2,3,configName,ref errs,ref warns);
  if(doc==null) doc=(SW.IModelDoc2)app.OpenDoc6(docPath,1,3,configName,ref errs,ref warns);
  if(doc==null) throw new Exception("open failed: errors="+errs+" warnings="+warns);
  var row=new Dictionary<string,object>();
  try{
   row["document"]=doc.GetPathName();
   var cfgNames=new List<string>();
   try{
    foreach(object o in (object[])doc.GetConfigurationNames()) cfgNames.Add(Convert.ToString(o));
   }catch(Exception ex){ cfgNames.Add("<error: "+ex.Message+">"); }
   row["configurations"]=cfgNames.ToArray();
   row["active_configuration"]=S(()=>doc.ConfigurationManager.ActiveConfiguration.Name);
   var em=(SW.IEquationMgr)doc.GetEquationMgr();
   if(em==null){ row["error"]="no equation manager"; return row; }
   int unit=-1; try{ unit=em.AngularEquationUnits; }catch{}
   row["angular_equation_units"]=unit;
   bool auto=false; try{ auto=em.AutomaticRebuild; }catch{}
   row["automatic_rebuild"]=auto;
   int n=0; try{ n=em.GetCount(); }catch(Exception ex){ row["count_error"]=ex.Message; }
   row["count"]=n;
   var rows=new List<object>();
   for(int i=0;i<n;i++){
    string eq=""; double val=0;
    try{ eq=em.get_Equation(i); }catch(Exception ex){ eq="<read error: "+ex.Message+">"; }
    try{ val=em.get_Value(i); }catch{}
    rows.Add(new{index=i,equation=eq,value=val});
   }
   row["equations"]=rows.ToArray();
  }
  finally{ try{ app.CloseDoc(doc.GetTitle()); }catch{} }
  return row;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[ProbeEquations]::Run([IO.Path]::GetFullPath($DocPath),[string]$ConfigName)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
