param(
    [Parameter(Mandatory=$true)][string]$SourceFolder,
    [Parameter(Mandatory=$true)][string]$TargetFolder,
    [Parameter(Mandatory=$true)][string]$WritesJson,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string[]]$SuppressMates = @(),
    [string]$SuppressMatesFile = ''
)
if($SuppressMatesFile){
    $txt=[IO.File]::ReadAllText([IO.Path]::GetFullPath($SuppressMatesFile),[Text.UTF8Encoding]::new($false))
    $SuppressMates=@()
    foreach($n in ($txt | ConvertFrom-Json)){ $SuppressMates+=[string]$n }
    Write-Output ("suppress list: "+($SuppressMates -join ' | '))
}
# Applies a list of dimension writes to a SCRATCH COPY of a template, rebuilds the
# assembly, and reports the mate errors that survive the rebuild. Never saves.
# WritesJson: [{"token":"10阀板","parameter":"D2@草图1","value_SI":0.031}, ...]
# All Chinese literals in the C# source are unicode escapes so this file stays pure ASCII.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "SolidWorks interop missing: $interop" }

$src=(Resolve-Path -LiteralPath $SourceFolder).Path
$dst=[IO.Path]::GetFullPath($TargetFolder)
if(Test-Path -LiteralPath $dst){ Remove-Item -LiteralPath $dst -Recurse -Force }
New-Item -ItemType Directory -Path $dst | Out-Null
Get-ChildItem -LiteralPath $src -File | Where-Object { $_.Extension -match '^\.(SLDASM|SLDPRT)$' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $dst }
Write-Output ("copied to: "+$dst)

$raw=[IO.File]::ReadAllText([IO.Path]::GetFullPath($WritesJson),[Text.UTF8Encoding]::new($false))
$writes=$raw | ConvertFrom-Json
$tokens=[string[]]@($writes | ForEach-Object { [string]$_.token })
$pars=  [string[]]@($writes | ForEach-Object { [string]$_.parameter })
$vals=  [double[]]@($writes | ForEach-Object { [double]$_.value_SI })
Write-Output ("writes: "+$tokens.Length)

$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class ApplyWritesProbe {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}

 static SW.IDimension Dim(SW.IModelDoc2 doc,string param){
  if(doc==null)return null;
  for(var f=(SW.IFeature)doc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   object disp=null; try{disp=f.GetFirstDisplayDimension();}catch{}
   int g=0;
   while(disp!=null&&g++<500){
    try{
     var d=(SW.IDimension)((SW.IDisplayDimension)disp).GetDimension2(0);
     if(d!=null){
      string fn=""; try{fn=d.FullName;}catch{}
      if(fn==param||fn.StartsWith(param+"@",StringComparison.Ordinal)) return d;
     }
    }catch{}
    try{disp=f.GetNextDisplayDimension(disp);}catch{disp=null;}
   }
  }
  return null;
 }

 static object[] MateErrors(SW.IModelDoc2 d){
  var rows=new List<object>();
  for(var f=(SW.IFeature)d.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature()){
    var t=S(()=>s.GetTypeName2());
    if(t!=null&&t.IndexOf("Mate",StringComparison.OrdinalIgnoreCase)>=0&&s.GetErrorCode()!=0)
     rows.Add(new{name=s.Name,type=t,error=s.GetErrorCode()});
   }
  }
  return rows.ToArray();
 }

 public static object Run(string asmPath,string[] tokens,string[] pars,double[] vals,string[] suppress){
  asmPath=Path.GetFullPath(asmPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(asmPath,2,1,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null)throw new Exception("open failed: errors="+e+" warnings="+w);
  var r=new Dictionary<string,object>();
  try{
   var assy=(SW.IAssemblyDoc)doc;
   r["assembly"]=doc.GetPathName();
   r["open_errors"]=e; r["open_warnings"]=w;
   doc.ForceRebuild3(false);
   r["baseline_mate_errors"]=MateErrors(doc);

   var comps=A(assy.GetComponents(false)).Cast<SW.IComponent2>().ToArray();
   var results=new List<object>();
   for(int i=0;i<tokens.Length;i++){
    var token=tokens[i]; var par=pars[i]; var val=vals[i];
    SW.IComponent2 comp=null;
    foreach(var c in comps){
     var p=S(()=>c.GetPathName());
     if(p.Length>0&&Path.GetFileName(p).Contains(token)){comp=c;break;}
    }
    if(comp==null){results.Add(new{token=token,parameter=par,found=false,error="component not found"});continue;}
    SW.IModelDoc2 pdoc=null;
    try{pdoc=comp.GetModelDoc2() as SW.IModelDoc2;}catch{}
    if(pdoc==null){results.Add(new{token=token,parameter=par,found=false,error="part doc unresolved"});continue;}
    var d=Dim(pdoc,par);
    if(d==null){results.Add(new{token=token,parameter=par,found=false,error="dimension not found"});continue;}
    double before=0; try{before=d.SystemValue;}catch{}
    int st=-999;
    try{st=d.SetSystemValue3(val,1,null);}catch(Exception ex){results.Add(new{token=token,parameter=par,found=true,set_error=ex.Message});continue;}
    double after=0; try{after=d.SystemValue;}catch{}
    results.Add(new{token=token,parameter=par,found=true,before_SI=before,requested_SI=val,after_SI=after,set_status=st});
   }
   r["writes"]=results.ToArray();
   r["rebuild_ok"]=doc.ForceRebuild3(false);
   r["mate_errors_after"]=MateErrors(doc);

   if(suppress!=null&&suppress.Length>0){
    var acted=new List<object>();
    foreach(var nm in suppress){
     SW.IFeature target=null;
     for(var f=(SW.IFeature)doc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
      for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature()){
       if(s.Name==nm){target=s;break;}
      }
      if(target!=null)break;
     }
     if(target==null){acted.Add(new{name=nm,found=false});continue;}
     bool ok=false;string err="";
     try{ok=target.SetSuppression2(0,1,null);}catch(Exception ex){err=ex.Message;}
     if(!ok){try{ok=target.SetSuppression(0);}catch(Exception ex){err=err+" | "+ex.Message;}}
     bool sup=false;try{sup=target.IsSuppressed();}catch{}
     acted.Add(new{name=nm,found=true,suppress_ok=ok,suppressed=sup,error=err});
    }
    r["suppress_actions"]=acted.ToArray();
    r["rebuild_after_suppress_ok"]=doc.ForceRebuild3(false);
    r["mate_errors_after_suppress"]=MateErrors(doc);
    r["rebuild_second_ok"]=doc.ForceRebuild3(false);
    r["mate_errors_after_suppress_2nd"]=MateErrors(doc);
    r["rebuild_third_ok"]=doc.ForceRebuild3(false);
    r["mate_errors_after_suppress_3rd"]=MateErrors(doc);
   }

   var tf=new List<object>();
   foreach(var c in comps){
    tf.Add(new{name=S(()=>c.Name2),transform=(double[])((SW.IMathTransform)c.Transform2).ArrayData});
   }
   r["final_transforms"]=tf.ToArray();

   // Interference snapshot of the same document state.
   try{
    var mgr=(SW.IInterferenceDetectionMgr)assy.InterferenceDetectionManager;
    if(mgr!=null){
     mgr.TreatCoincidenceAsInterference=false;
     mgr.IncludeMultibodyPartInterferences=false;
     var rawI=mgr.GetInterferences();
     var arrI=rawI as Array;
     int ni=arrI==null?0:arrI.Length;
     var irows=new List<object>();
     for(int i=0;i<ni;i++){
      var it=arrI.GetValue(i) as SW.IInterference;
      if(it==null)continue;
      var names=new List<string>();
      try{ var csx=it.Components;
       if(csx is Array) foreach(object o in (Array)csx){ var c=o as SW.IComponent2; names.Add(c==null?"<null>":S(()=>c.Name2)); }
      }catch{}
      double v=0; try{v=it.Volume*1e9;}catch{}
      irows.Add(new{components=names.ToArray(),volume_mm3=v});
     }
     r["interferences"]=irows.ToArray();
     try{mgr.Done();}catch{}
    }
   }catch(Exception ex){ r["interference_error"]=ex.Message; }

   r["saved"]=false;
  }
  finally{try{app.CloseDoc(doc.GetTitle());}catch{}}
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$asm=Get-ChildItem -LiteralPath $dst -Filter '*.SLDASM' -File | Select-Object -First 1 -ExpandProperty FullName
if(-not $asm){ throw "no assembly found in $dst" }
$result=[ApplyWritesProbe]::Run($asm,$tokens,$pars,$vals,[string[]]$SuppressMates)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
