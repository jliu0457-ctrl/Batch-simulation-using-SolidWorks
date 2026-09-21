param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Adds the seven design global variables to the V7 ASSEMBLY.
# Only globals are added, no dimension equations: every cross-part dimension is already
# driven by its own part's equation, and a second equation on the same dimension would
# double-drive it.  Cross-part consistency is a positioning problem (mates), not a
# dimension problem.  ASCII only; Chinese uses unicode escapes in the C# source.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "SolidWorks interop missing: $interop" }
$source=@'
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class BuildV7Assembly {
 // angular values are arc-seconds, matching the part equations
 static readonly string[][] GLOBALS = new string[][]{
  new[]{"c","32"}, new[]{"e","3.7"},
  new[]{"phi_arcsec","29700"}, new[]{"alpha_arcsec","127800"},
  new[]{"s","194.37"}, new[]{"bm","7.5"}, new[]{"ds","45"}
 };

 public static object Run(string assemblyPath){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int errs=0,warns=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,"\u5f00\u5ea645\u00b0",ref errs,ref warns);
  if(doc==null) throw new Exception("open failed: errors="+errs+" warnings="+warns);
  var row=new Dictionary<string,object>();
  try{
   var em=(SW.IEquationMgr)doc.GetEquationMgr();
   if(em==null) throw new Exception("no equation manager");
   var added=new List<object>();
   foreach(var g in GLOBALS){
    string eq="\""+g[0]+"\" = "+g[1];
    int idx=-1; string err="";
    try{ idx=em.Add(-1,eq); }catch(Exception ex){ err=ex.Message; }
    added.Add(new{kind="global",equation=eq,index=idx,error=err});
   }
   int count=0; try{ count=em.GetCount(); }catch{}
   row["equation_count"]=count;
   row["added"]=added.ToArray();
   row["rebuild_ok"]=doc.ForceRebuild3(false);
   int se=0,sw=0;
   row["save_ok"]=doc.Save3(1,ref se,ref sw);
   row["save_errors"]=se;
  }catch(Exception ex){ row["error"]=ex.ToString(); }
  finally{ try{ app.CloseDoc(doc.GetTitle()); }catch{} }
  return row;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[BuildV7Assembly]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
