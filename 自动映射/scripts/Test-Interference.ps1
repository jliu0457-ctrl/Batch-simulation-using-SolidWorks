param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Interference detection. Opens the assembly with OpenDoc6(..., options=1),
# force-rebuilds so the mate solver has placed every component, then runs SolidWorks'
# own Interference Detection and reports every interfering pair with its volume.
# TreatCoincidenceAsInterference is switched OFF: faces that merely touch by design
# (every mating face in this valve) must not be reported as interference.
# All Chinese literals in the C# source are unicode escapes so this file stays pure ASCII.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "SolidWorks interop missing: $interop" }
$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class InterferenceProbe {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}

 public static object Run(string assemblyPath){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int e=0,w=0;
  // Writable open: interference detection builds temporary interference bodies and
  // crashed SolidWorks outright when the document was opened read-only.
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null)throw new Exception("open failed: errors="+e+" warnings="+w);
  var r=new Dictionary<string,object>();
  try{
   var assy=(SW.IAssemblyDoc)doc;
   r["assembly"]=doc.GetPathName();
   r["open_errors"]=e; r["open_warnings"]=w;
   r["rebuild_ok"]=doc.ForceRebuild3(false);
   var comps=new List<string>();
   foreach(var o in A(assy.GetComponents(false))){
    var c=o as SW.IComponent2; if(c!=null) comps.Add(S(()=>c.Name2));
   }
   r["components"]=comps.ToArray();

   var mgr=(SW.IInterferenceDetectionMgr)assy.InterferenceDetectionManager;
   if(mgr==null)throw new Exception("InterferenceDetectionManager unavailable");
   mgr.TreatCoincidenceAsInterference=false;   // touching mating faces are by design
   mgr.IncludeMultibodyPartInterferences=false; // component-vs-component only
   object raw=mgr.GetInterferences();
   var arr=raw as Array;
   int n=arr==null?0:arr.Length;
   r["interference_count"]=n;
   var rows=new List<object>();
   for(int i=0;i<n;i++){
    SW.IInterference it=null;
    try{it=arr.GetValue(i) as SW.IInterference;}catch(Exception ex){rows.Add(new{index=i,error=ex.Message});continue;}
    if(it==null){rows.Add(new{index=i,error="null interference"});continue;}
    var names=new List<string>();
    try{
     var cs=it.Components;
     if(cs is Array) foreach(object o in (Array)cs){ var c=o as SW.IComponent2; names.Add(c==null?"<null>":S(()=>c.Name2)); }
    }catch(Exception ex){names.Add("<components err:"+ex.Message+">");}
    double vol=0; try{vol=it.Volume*1e9;}catch{}   // m^3 -> mm^3
    // The interference body is returned in assembly coordinates. Its box locates the
    // overlap inside the valve, which is what decides whether it perturbs the flow.
    double[] box=null;
    try{
     var b=it.GetInterferenceBody() as SW.IBody2;
     if(b!=null) box=b.GetBodyBox() as double[];
    }catch{}
    object boxMm=null;
    if(box!=null&&box.Length==6){
     boxMm=new{
      min_mm=new[]{box[0]*1000,box[1]*1000,box[2]*1000},
      max_mm=new[]{box[3]*1000,box[4]*1000,box[5]*1000},
      size_mm=new[]{(box[3]-box[0])*1000,(box[4]-box[1])*1000,(box[5]-box[2])*1000},
      center_mm=new[]{(box[0]+box[3])/2*1000,(box[1]+box[4])/2*1000,(box[2]+box[5])/2*1000}
     };
    }
    rows.Add(new{index=i,components=names.ToArray(),volume_mm3=vol,box=boxMm});
   }
   r["interferences"]=rows.ToArray();
   try{mgr.Done();}catch{}
   r["finished"]=true;
  }
  finally{try{app.CloseDoc(doc.GetTitle());}catch{}}
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[InterferenceProbe]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
