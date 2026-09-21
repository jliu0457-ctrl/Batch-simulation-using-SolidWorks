param(
    [Parameter(Mandatory=$true)][string]$Folder,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Adds seven global variables and 33 dimension equations to a COPY of the V6 template
# (never the template itself).  All values are the baseline design, so the geometry must
# not change: the only difference is that the values now live in one place per document.
# The whole file is ASCII; Chinese dimension names use unicode escapes in the C# source.
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

public static class BuildV7 {
 static string S(Func<string> f){try{return f();}catch{return "";}}

 // global name -> baseline value.
 // Lengths are millimetres.  SolidWorks reads an angular value in an equation as
 // ARC-SECONDS in this document: writing 8.25 produced 0.0022917 deg, exactly 8.25
 // arc-sec (a factor of 3600), and AngularEquationUnits did not change it.  The two
 // angle globals therefore carry their unit in the name.  8.25 deg = 29700 arc-sec,
 // 35.5 deg = 127800 arc-sec.
 static readonly string[][] GLOBALS = new string[][]{
  new[]{"c","32"}, new[]{"e","3.7"},
  new[]{"phi_arcsec","29700"}, new[]{"alpha_arcsec","127800"},
  new[]{"s","194.37"}, new[]{"bm","7.5"}, new[]{"ds","45"}
 };

 // token -> equations, applied only to the part whose file name contains the token
 static readonly string[][][] PART_EQ = new string[][][]{
  new[]{ new[]{"03\u9600\u4f53"},
   new[]{"D2@\u8349\u56fe3","\"c\""},
   new[]{"D30@\u8349\u56fe4","\"e\""},
   new[]{"D4@\u8349\u56fe6","\"e\""},
   new[]{"D3@\u8349\u56fe5","\"phi_arcsec\""},
   new[]{"D2@\u8349\u56fe5","\"alpha_arcsec\"/2"},
   new[]{"D25@\u8349\u56fe4","\"ds\""},
   new[]{"D5@\u8349\u56fe5","\"s\""} },
  new[]{ new[]{"08\u5927\u57ab\u7247"},
   new[]{"D10@\u8349\u56fe1","\"c\""},
   new[]{"D5@\u8349\u56fe1","\"phi_arcsec\""},
   new[]{"D7@\u8349\u56fe1","\"alpha_arcsec\""},
   new[]{"D6@\u8349\u56fe1","\"s\""},
   new[]{"D8@\u8349\u56fe1","\"s\"-3.15"} },
  new[]{ new[]{"09\u5bc6\u5c01\u5708"},
   new[]{"D3@\u8349\u56fe1","\"c\""},
   new[]{"D9@\u8349\u56fe1","\"e\""},
   new[]{"D5@\u8349\u56fe1","\"phi_arcsec\""},
   new[]{"D6@\u8349\u56fe1","\"alpha_arcsec\"/2"},
   new[]{"D7@\u8349\u56fe1","\"s\""},
   new[]{"D2@\u8349\u56fe1","\"bm\""},
   new[]{"D8@\u8349\u56fe1","\"ds\""} },
  new[]{ new[]{"10\u538b\u677f"},
   new[]{"D2@\u8349\u56fe1","\"c\""},
   new[]{"D4@\u8349\u56fe1","\"phi_arcsec\""},
   new[]{"D7@\u8349\u56fe1","\"alpha_arcsec\"/2"},
   new[]{"D5@\u8349\u56fe1","\"s\""},
   new[]{"D8@\u8349\u56fe1","\"s\"/2-1.575"} },
  new[]{ new[]{"11\u8776\u677f"},
   new[]{"D2@\u8349\u56fe1","\"c\""},
   new[]{"D1@\u8349\u56fe1","\"e\""},
   new[]{"D1@\u57fa\u51c6\u97622","\"e\""},
   new[]{"D2@\u8349\u56fe3","\"phi_arcsec\""},
   new[]{"D4@\u8349\u56fe3","\"alpha_arcsec\"/2"},
   new[]{"D3@\u8349\u56fe3","\"s\""},
   new[]{"D2@\u8349\u56fe2","\"ds\""},
   new[]{"D5@\u8349\u56fe3","\"s\"/2-1.575"} },
  new[]{ new[]{"04\u9600\u8f74"},
   new[]{"D4@\u8349\u56fe1","\"ds\""} }
 };

 public static object Run(string folder){
  folder=Path.GetFullPath(folder);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var report=new Dictionary<string,object>();
  var files=new List<object>();

  foreach(string path in Directory.GetFiles(folder,"*.SLDPRT").OrderBy(x=>x)){
   string fname=Path.GetFileName(path);
   string token=null;
   foreach(var grp in PART_EQ){
    if(fname.Contains(grp[0][0])){token=grp[0][0];break;}
   }
   if(token==null) continue;                       // caps and other parts untouched
   int errs=0,warns=0;
   var doc=(SW.IModelDoc2)app.OpenDoc6(path,1,1,"",ref errs,ref warns);
   if(doc==null){ files.Add(new{file=fname,opened=false,error="open failed "+errs}); continue; }
   var row=new Dictionary<string,object>();
   row["file"]=fname;
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
    foreach(var grp in PART_EQ){
     if(grp[0][0]!=token) continue;
     for(int i=1;i<grp.Length;i++){
      string eq="\""+grp[i][0]+"\" = "+grp[i][1];
      int idx=-1; string err="";
      try{ idx=em.Add(-1,eq); }catch(Exception ex){ err=ex.Message; }
      added.Add(new{kind="dimension",equation=eq,index=idx,error=err});
     }
    }
    doc.ForceRebuild3(false);
    int hard=0; try{ hard=em.GetCount(); }catch{}
    row["equation_count"]=hard;
    row["added"]=added.ToArray();
    row["rebuild_ok"]=doc.ForceRebuild3(false);
    int se=0,sw=0;
    row["save_ok"]=doc.Save3(1,ref se,ref sw);
    row["save_errors"]=se;
   }catch(Exception ex){ row["error"]=ex.ToString(); }
   files.Add(row);
   try{ app.CloseDoc(doc.GetTitle()); }catch{}
  }
  report["parts"]=files.ToArray();
  return report;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[BuildV7]::Run([IO.Path]::GetFullPath($Folder))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
