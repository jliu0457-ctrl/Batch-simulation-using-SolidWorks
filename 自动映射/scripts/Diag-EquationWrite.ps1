param(
    [Parameter(Mandatory=$true)][string]$Folder,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Reproduces the adapter's equation-write conditions in isolation, logging every step, so
# the difference between this and the standalone Set-Globals.ps1 (which works) shows up.
# Phase A: open all six parts writable, exactly like Context does.
# Phase B: on the body part, read the equation, set it, read it back, logging both.
# ASCII only; Chinese uses unicode escapes in the C# source.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ $interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll' }
$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class DiagEqWrite {
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static string ActiveTitle(SW.ISldWorks a){try{var d=a.ActiveDoc as SW.IModelDoc2;return d==null?"":S(()=>d.GetTitle());}catch{return "";}}
 static readonly string[] Tokens = { "03\u9600\u4f53","04\u9600\u8f74","08\u5927\u57ab\u7247",
                                     "09\u5bc6\u5c01\u5708","10\u538b\u677f","11\u8776\u677f" };

 static int Idx(SW.IModelDoc2 doc,string name){
  var em=(SW.IEquationMgr)doc.GetEquationMgr();
  string want="\""+name+"\"";
  for(int i=0,n=em.GetCount();i<n;i++){
   string eq=""; try{ eq=em.get_Equation(i); }catch{}
   if(eq.TrimStart().StartsWith(want,StringComparison.Ordinal)) return i;
  }
  return -1;
 }

 public static object Run(string folder){
  folder=Path.GetFullPath(folder);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var log=new List<object>();
  var opened=new List<SW.IModelDoc2>();

  // Phase A: open all six parts writable, in the same order the adapter uses
  foreach(string path in Directory.GetFiles(folder,"*.SLDPRT").OrderBy(x=>x)){
   string fname=Path.GetFileName(path);
   if(!Tokens.Any(t=>fname.Contains(t))) continue;
   int e=0,w=0;
   var d=(SW.IModelDoc2)app.OpenDoc6(path,1,1,"",ref e,ref w);
   log.Add(new{phase="open",file=fname,ok=d!=null,errors=e,active=ActiveTitle(app)});
   if(d!=null) opened.Add(d);
  }
  log.Add(new{phase="all_opened",count=opened.Count,active_doc=ActiveTitle(app)});

  // Phase B: write c on the body part
  var target=opened.FirstOrDefault(d=>S(()=>d.GetTitle()).Contains("03"));
  if(target==null){ log.Add(new{phase="target",found=false}); }
  else{
   log.Add(new{phase="target",found=true,title=S(()=>target.GetTitle()),
               is_active=ActiveTitle(app)==S(()=>target.GetTitle())});
   int i=Idx(target,"c");
   log.Add(new{phase="index_before",index=i});
   if(i>=0){
    var em=(SW.IEquationMgr)target.GetEquationMgr();
    string before=""; try{ before=em.get_Equation(i); }catch{}
    log.Add(new{phase="before",text=before,value=S(()=>em.get_Value(i).ToString())});
    string want="\"c\" = 31";
    string err="";
    try{ em.set_Equation(i,want); }catch(Exception ex){ err=ex.Message; }
    log.Add(new{phase="set",wrote=want,error=err});
    string after=""; try{ after=em.get_Equation(i); }catch{}
    string after2=""; try{ after2=em.get_Equation(Idx(target,"c")); }catch{}
    log.Add(new{phase="after",text=after,text_by_index=after2,
                index_now=Idx(target,"c")});
    // now try activating then writing again
    int ae=0;
    try{ app.ActivateDoc3(target.GetTitle(),false,0,ref ae); }catch(Exception ex){ err=ex.Message; }
    log.Add(new{phase="activate",errors=ae,active=ActiveTitle(app)});
    int j=Idx(target,"c");
    if(j>=0){
     try{ ((SW.IEquationMgr)target.GetEquationMgr()).set_Equation(j,"\"c\" = 31"); }catch(Exception ex){ err=ex.Message; }
     log.Add(new{phase="after_activate",text=S(()=>((SW.IEquationMgr)target.GetEquationMgr()).get_Equation(j))});
    }
   }
  }
  foreach(var d in opened){ try{ app.CloseDoc(d.GetTitle()); }catch{} }
  var r=new Dictionary<string,object>();
  r["log"]=log.ToArray();
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[DiagEqWrite]::Run([IO.Path]::GetFullPath($Folder))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
