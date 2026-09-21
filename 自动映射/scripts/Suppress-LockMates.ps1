param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string[]]$Names = @(),
    [switch]$Apply,
    [switch]$IncludeUnion
)
# Suppresses the named lock mates in an assembly; with -Apply it also saves the document.
# Without -Apply it only reports their current state.
#
# Background: the four "lock" mates freeze a pair's relative position.  Lock 1/2/3 pin the
# gasket, the seal ring and the pressure plate to the disc; lock 4 pins the shaft to the disc.
# A lock holds the pair's *position* but cannot stop the pair's *shapes* from changing when a
# driven dimension changes, which is what produces the measured overlap.  Lock 4 also
# over-defines the assembly, so the concentric shaft-to-bore mate cannot be added while it is
# active (swAddMateError_OverDefinedAssembly).
#
# ASCII only - every Chinese string is built from code points inside the C#.  A Chinese
# literal in this file would be decoded with the wrong codepage by PowerShell 5.1.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "interop missing: $interop" }
$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class LockSuppressor {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static string CP(params int[] cps){ var s=""; foreach(int c in cps) s+=((char)c).ToString(); return s; }

 // A feature chain must be advanced with GetNextSubFeature() while walking sub-features and
 // GetNextFeature() at the top level.  Using GetNextFeature() everywhere silently stops after
 // the first child - which is how an earlier walker saw one mate in an assembly holding twenty.
 static void WalkAll(SW.IFeature f,bool asSub,List<SW.IFeature> outp){
  while(f!=null){
   outp.Add(f);
   var sub=(SW.IFeature)f.GetFirstSubFeature();
   if(sub!=null) WalkAll(sub,true,outp);
   f = asSub ? (SW.IFeature)f.GetNextSubFeature() : (SW.IFeature)f.GetNextFeature();
  }
 }
 static List<SW.IFeature> All(SW.IModelDoc2 doc){
  var list=new List<SW.IFeature>();
  WalkAll((SW.IFeature)doc.FirstFeature(),false,list);
  return list;
 }
 static object[] MateRows(List<SW.IFeature> all){
  var rows=new List<object>();
  foreach(var f in all){
   string t=S(()=>f.GetTypeName2());
   if(t.IndexOf("Mate",StringComparison.OrdinalIgnoreCase)<0 || t=="MateGroup") continue;
   rows.Add(new Dictionary<string,object>{{"name",S(()=>f.Name)},{"type",t},
     {"suppressed",S(()=>f.IsSuppressed()?"1":"0")},{"error",S(()=>f.GetErrorCode().ToString())}});
  }
  return rows.ToArray();
 }

 public static object Run(string assemblyPath,string[] names,bool apply,bool includeUnion){
  if(names==null||names.Length==0){
   string lp=CP(0x9501,0x5b9a);
   names=new[]{lp+"1",lp+"2",lp+"3",lp+"4"};
  }
  // -IncludeUnion also drops the disc<->gasket coincident mate (0x91cd 0x5408 + "4"),
  // used to test whether that mate is what pins the gasket in place.
  if(includeUnion){
   var list=new List<string>(names);
   list.Add(CP(0x91cd,0x5408)+"4");   // disc <-> gasket coincident
   list.Add(CP(0x540c,0x5fc3)+"8");   // disc <-> gasket concentric
   list.Add(CP(0x5e73,0x884c)+"5");   // disc <-> gasket parallel
   names=list.ToArray();
  }
  string configName=CP(0x5f00,0x5ea6)+"45"+CP(0xb0);
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var rows=new List<object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,configName,ref e,ref w);
  if(doc==null){ r["error"]="open failed errors="+e+" warnings="+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   var all=All(doc);
   r["feature_count"]=all.Count;
   r["mates_before"]=MateRows(all);

   foreach(string name in names){
    SW.IFeature feature=null;
    foreach(var f in all){ if(String.Equals(S(()=>f.Name),name,StringComparison.Ordinal)){ feature=f; break; } }
    if(feature==null){ rows.Add(new Dictionary<string,object>{{"name",name},{"found",false}}); continue; }
    if(S(()=>feature.IsSuppressed()?"1":"0")=="1"){
     rows.Add(new Dictionary<string,object>{{"name",name},{"found",true},{"already_suppressed",true}}); continue;
    }
    if(!apply){ rows.Add(new Dictionary<string,object>{{"name",name},{"found",true},{"would_suppress",true}}); continue; }
    bool ok=false;
    try{ ok=feature.SetSuppression2(0,1,null); }catch(Exception ex){ rows.Add(new Dictionary<string,object>{{"name",name},{"error",ex.Message}}); continue; }
    if(!ok){ try{ ok=feature.SetSuppression(0); }catch{} }
    rows.Add(new Dictionary<string,object>{{"name",name},{"found",true},
      {"suppress_call_ok",ok},{"suppressed",S(()=>feature.IsSuppressed()?"1":"0")=="1"}});
   }
   r["actions"]=rows.ToArray();
   r["rebuild_ok"]=doc.ForceRebuild3(false);

   if(apply){
    int se=0,sw=0;
    r["save_ok"]=doc.Save3(1,ref se,ref sw);
    r["save_errors"]=se; r["save_warnings"]=sw;
   }
   r["mates_after"]=MateRows(All(doc));
  }
  catch(Exception ex){ r["exception"]=ex.ToString(); }
  finally{ try{app.CloseDoc(title);}catch{} }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
try{ Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop) -ErrorAction Stop }
catch{ Write-Host "=== Add-Type failed ==="; Write-Host $_.Exception.Message
        if($_.Exception.InnerException){ Write-Host "--- inner ---"; Write-Host $_.Exception.InnerException.Message }; exit 1 }
$nameArg = $null
if($Names -and $Names.Count -gt 0){ $nameArg = [string[]]$Names }
$result=[LockSuppressor]::Run([IO.Path]::GetFullPath($AssemblyPath),$nameArg,[bool]$Apply,[bool]$IncludeUnion)
$json=$result | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
