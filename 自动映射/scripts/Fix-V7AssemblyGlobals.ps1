param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Adds the seven globals to the V7 assembly and proves where they are lost, if they are:
# add -> read back in memory -> Save() -> close -> reopen -> read back.
# Uses Save() rather than Save3 so nothing about the save options can hide the result.
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

public static class FixV7Asm {
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static readonly string[][] GLOBALS = new string[][]{
  new[]{"c","32"}, new[]{"e","3.7"},
  new[]{"phi_arcsec","29700"}, new[]{"alpha_arcsec","127800"},
  new[]{"s","194.37"}, new[]{"bm","7.5"}, new[]{"ds","45"}
 };

 static object Dump(SW.IModelDoc2 doc){
  var row=new Dictionary<string,object>();
  SW.IEquationMgr em=null;
  try{ em=(SW.IEquationMgr)doc.GetEquationMgr(); }catch(Exception ex){ row["mgr_error"]=ex.Message; return row; }
  if(em==null){ row["mgr_error"]="null"; return row; }
  int n=-1; try{ n=em.GetCount(); }catch(Exception ex){ row["count_error"]=ex.Message; }
  row["count"]=n;
  var list=new List<string>();
  for(int i=0;i<n;i++){ try{ list.Add(em.get_Equation(i)); }catch(Exception ex){ list.Add("<err:"+ex.Message+">"); } }
  row["equations"]=list.ToArray();
  row["active_configuration"]=S(()=>doc.ConfigurationManager.ActiveConfiguration.Name);
  return row;
 }

 public static object Run(string assemblyPath){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  int e1=0,w1=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,"",ref e1,ref w1);
  if(doc==null){ r["open_error"]="errors="+e1+" warnings="+w1; return r; }
  try{
   r["active_configuration_at_open"]=S(()=>doc.ConfigurationManager.ActiveConfiguration.Name);
   r["before"]=Dump(doc);
   var em=(SW.IEquationMgr)doc.GetEquationMgr();
   var added=new List<object>();
   foreach(var g in GLOBALS){
    string eq="\""+g[0]+"\" = "+g[1];
    int idx=-1; string err=""; string how="";
    // Add3 carries a configuration option; plain Add does not, and on a document with
    // more than one configuration the equation then fails to persist.
    // 0 = swAllConfigurations.
    try{ idx=em.Add3(-1,eq,true,0,null); how="Add3(all)"; }
    catch(Exception ex){
     err="Add3: "+ex.Message;
     try{ idx=em.Add(-1,eq); how="Add"; }catch(Exception ex2){ err+=" | Add: "+ex2.Message; }
    }
    added.Add(new{equation=eq,index=idx,error=err,method=how});
   }
   r["added"]=added.ToArray();
   r["after_add_before_rebuild"]=Dump(doc);
   doc.ForceRebuild3(false);
   r["after_rebuild"]=Dump(doc);
   try{ doc.Save(); r["save_ok"]=true; }catch(Exception ex){ r["save_ok"]=false; r["save_error"]=ex.Message; }
  }catch(Exception ex){ r["error"]=ex.ToString(); }
  finally{ try{ app.CloseDoc(doc.GetTitle()); }catch{} }

  // reopen from disk
  int e2=0,w2=0;
  var doc2=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,3,"",ref e2,ref w2);
  if(doc2==null){ r["reopen_error"]="errors="+e2+" warnings="+w2; return r; }
  try{ r["after_reopen"]=Dump(doc2); }
  finally{ try{ app.CloseDoc(doc2.GetTitle()); }catch{} }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[FixV7Asm]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
