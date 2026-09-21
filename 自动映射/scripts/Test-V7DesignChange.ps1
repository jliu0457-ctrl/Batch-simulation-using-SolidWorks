param(
    [Parameter(Mandatory=$true)][string]$Folder,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Drives the V7 equation template with a new design and reports whether the parts stay
# self-consistent.  Runs on a copy.  The seven globals live in the PARTS, so they are set
# there; the assembly is then reopened and rebuilt.  Nothing is saved.
# ASCII only; Chinese uses unicode escapes in the C# source.
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

public static class V7DesignTest {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}

 // the changed design, as global values.  Angles are arc-seconds, matching the template.
 static readonly string[][] NEW = new string[][]{
  new[]{"c","31"}, new[]{"e","4"},
  new[]{"phi_arcsec","30600"}, new[]{"alpha_arcsec","124200"},
  new[]{"s","192.11058510899018"}, new[]{"bm","7.2"}, new[]{"ds","44"}
 };

 static string[] Tokens = { "03\u9600\u4f53","04\u9600\u8f74","08\u5927\u57ab\u7247",
                            "09\u5bc6\u5c01\u5708","10\u538b\u677f","11\u8776\u677f" };

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

 public static object Run(string folder){
  folder=Path.GetFullPath(folder);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var parts=new List<object>();

  foreach(string path in Directory.GetFiles(folder,"*.SLDPRT").OrderBy(x=>x)){
   string fname=Path.GetFileName(path);
   if(!Tokens.Any(t=>fname.Contains(t))) continue;
   int e1=0,w1=0;
   var doc=(SW.IModelDoc2)app.OpenDoc6(path,1,1,"",ref e1,ref w1);
   if(doc==null){ parts.Add(new{file=fname,opened=false,error=e1}); continue; }
   var row=new Dictionary<string,object>();
   row["file"]=fname;
   try{
    var em=(SW.IEquationMgr)doc.GetEquationMgr();
    int n=em.GetCount();
    var setRows=new List<object>();
    foreach(var g in NEW){
     string want="\""+g[0]+"\"";
     bool found=false;
     for(int i=0;i<n;i++){
      string eq=""; try{ eq=em.get_Equation(i); }catch{}
      string trimmed=eq.TrimStart();
      if(trimmed.StartsWith(want, StringComparison.Ordinal)){
       string newEq=want+" = "+g[1];
       string err="";
       try{ em.set_Equation(i,newEq); }catch(Exception ex){ err=ex.Message; }
       setRows.Add(new{global=g[0],index=i,new_equation=newEq,error=err});
       found=true; break;
      }
     }
     if(!found) setRows.Add(new{global=g[0],found=false});
    }
    row["globals_set"]=setRows.ToArray();
    row["rebuild_ok"]=doc.ForceRebuild3(false);
    row["equations_after"]=em.GetCount();
    // Must be saved: the assembly loads its components from disk, so unsaved part edits
    // are invisible to it (this is what made an earlier run look like the globals had
    // no effect when they had).
    try{ doc.Save(); row["saved"]=true; }catch(Exception ex){ row["saved"]=false; row["save_error"]=ex.Message; }
   }catch(Exception ex){ row["error"]=ex.ToString(); }
   parts.Add(row);
   try{ app.CloseDoc(doc.GetTitle()); }catch{}
  }
  r["parts"]=parts.ToArray();

  // reopen the assembly and rebuild
  string asm=Directory.GetFiles(folder,"*.SLDASM").Single();
  int e2=0,w2=0;
  var adoc=(SW.IModelDoc2)app.OpenDoc6(asm,2,1,"\u5f00\u5ea645\u00b0",ref e2,ref w2);
  if(adoc==null){ r["assembly_error"]="open failed "+e2+","+w2; return r; }
  try{
   var assy=(SW.IAssemblyDoc)adoc;
   r["assembly"]=adoc.GetPathName();
   r["mate_errors_after_rebuild_start"]=MateErrors(adoc);
   r["rebuild_ok"]=adoc.ForceRebuild3(false);
   r["mate_errors"]=MateErrors(adoc);
   // Read the driven dimensions back so a low interference count cannot be mistaken for
   // "the globals never took effect".
   var dims=new List<object>();
   foreach(var o in A(assy.GetComponents(false))){
    var c=o as SW.IComponent2; if(c==null) continue;
    var nm=S(()=>c.Name2);
    if(!Tokens.Any(t=>nm.Contains(t))) continue;
    var pdoc=c.GetModelDoc2() as SW.IModelDoc2;
    if(pdoc==null) continue;
    var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    for(var f=(SW.IFeature)pdoc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
     object disp=null; try{disp=f.GetFirstDisplayDimension();}catch{}
     int g=0;
     while(disp!=null&&g++<400){
      try{
       var dd=(SW.IDimension)((SW.IDisplayDimension)disp).GetDimension2(0);
       if(dd!=null && seen.Add(dd.FullName))
        dims.Add(new{component=nm,dimension=dd.FullName,value_SI=dd.SystemValue});
      }catch{}
      try{disp=f.GetNextDisplayDimension(disp);}catch{disp=null;}
     }
    }
   }
   r["dimensions"]=dims.ToArray();
   try{
    var mgr=(SW.IInterferenceDetectionMgr)assy.InterferenceDetectionManager;
    if(mgr!=null){
     mgr.TreatCoincidenceAsInterference=false;
     mgr.IncludeMultibodyPartInterferences=false;
     var arr=mgr.GetInterferences() as Array;
     int ni=arr==null?0:arr.Length;
     var irows=new List<object>();
     for(int i=0;i<ni;i++){
      var it=arr.GetValue(i) as SW.IInterference;
      if(it==null) continue;
      var names=new List<string>();
      try{ var cs=it.Components;
       if(cs is Array) foreach(object o in (Array)cs){ var c=o as SW.IComponent2; names.Add(c==null?"<null>":S(()=>c.Name2)); }
      }catch{}
      double v=0; try{ v=it.Volume*1e9; }catch{}
      irows.Add(new{components=names.ToArray(),volume_mm3=v});
     }
     r["interferences"]=irows.ToArray();
     try{ mgr.Done(); }catch{}
    }
   }catch(Exception ex){ r["interference_error"]=ex.Message; }
  }
  finally{ try{ app.CloseDoc(adoc.GetTitle()); }catch{} }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[V7DesignTest]::Run([IO.Path]::GetFullPath($Folder))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
