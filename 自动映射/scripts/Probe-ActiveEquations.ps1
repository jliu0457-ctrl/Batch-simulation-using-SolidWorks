param(
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Read-only.  Dumps every equation (global variables first, then the rest) of whichever
# document is active in SolidWorks, plus the document's save flag.  Used while the session
# cannot open new files.
# ASCII only; Chinese appears as unicode escapes in the C# source.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "interop missing: $interop" }
$source=@'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class ActiveEqProbe {
 static string S(Func<string> f){try{return f();}catch{return "";}}
 public static object Run(){
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var doc=app.ActiveDoc as SW.IModelDoc2;
  if(doc==null){ r["error"]="no active document"; return r; }
  r["title"]=S(()=>doc.GetTitle());
  r["path"]=S(()=>doc.GetPathName());
  r["dirty"]=S(()=>doc.GetSaveFlag().ToString());
  SW.IEquationMgr em=null;
  try{ em=(SW.IEquationMgr)doc.GetEquationMgr(); }catch(Exception ex){ r["error"]="no equation manager: "+ex.Message; return r; }
  int n=0; try{ n=em.GetCount(); }catch{}
  r["count"]=n;
  var rows=new List<object>();
  for(int i=0;i<n;i++){
   string eq=""; double val=0;
   try{ eq=em.get_Equation(i); }catch{}
   try{ val=em.get_Value(i); }catch{}
   rows.Add(new Dictionary<string,object>{{"i",i},{"equation",eq},
     {"value",Math.Round(val,6)}});
  }
  r["equations"]=rows.ToArray();
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
try{
    Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop) -ErrorAction Stop
}catch{
    Write-Host "=== Add-Type failed ==="; Write-Host $_.Exception.Message
    if($_.Exception.InnerException){ Write-Host "--- inner ---"; Write-Host $_.Exception.InnerException.Message }
    exit 1
}
$result=[ActiveEqProbe]::Run()
$json=$result | ConvertTo-Json -Depth 12
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
