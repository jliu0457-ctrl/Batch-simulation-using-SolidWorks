param(
    [Parameter(Mandatory=$true)][string]$SourceFolder,
    [Parameter(Mandatory=$true)][string]$TargetFolder,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$DeleteCaps
)
# Controlled experiment. Copies a template into a SCRATCH folder, opens the assembly
# WRITABLE, optionally deletes the four cap components, rebuilds, records mate errors,
# then closes WITHOUT SAVING. The source template is never touched.
# All Chinese literals are unicode escapes so this file stays pure ASCII.
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

$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class CapRemovalTest {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}

 static object[] MateErrors(SW.IModelDoc2 d){
  var rows=new List<object>();
  for(var f=(SW.IFeature)d.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature()){
    var t=S(()=>s.GetTypeName2());
    if(t!=null&&t.IndexOf("Mate",StringComparison.OrdinalIgnoreCase)>=0)
     rows.Add(new{name=s.Name,type=t,error=s.GetErrorCode()});
   }
  }
  return rows.ToArray();
 }

 static object[] Comps(SW.IAssemblyDoc a){
  var rows=new List<object>();
  foreach(var o in A(a.GetComponents(false))){
   var c=o as SW.IComponent2; if(c==null) continue;
   rows.Add(new{name=S(()=>c.Name2),suppression=S(()=>c.GetSuppression().ToString())});
  }
  return rows.ToArray();
 }

 public static object Run(string assemblyPath,bool deleteCaps){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null)throw new Exception("writable open failed: errors="+e+" warnings="+w);
  var r=new Dictionary<string,object>();
  try{
   var assy=(SW.IAssemblyDoc)doc;
   r["assembly"]=doc.GetPathName();
   r["open_errors"]=e; r["open_warnings"]=w;
   r["before_components"]=Comps(assy);
   r["before_mates"]=MateErrors(doc);
   r["before_rebuild_ok"]=doc.ForceRebuild3(false);
   r["before_rebuild_mates"]=MateErrors(doc);

   if(deleteCaps){
    var tokens=new[]{"\u5c01\u76d61","\u5c01\u76d62","\u5c01\u76d63","\u5c01\u76d64"};
    doc.ClearSelection2(true);
    int sel=0; var selNames=new List<string>();
    foreach(var o in A(assy.GetComponents(false))){
     var c=o as SW.IComponent2; if(c==null) continue;
     var nm=S(()=>c.Name2);
     if(!tokens.Any(t=>nm.Contains(t))) continue;
     bool ok=false; try{ok=c.Select4(true,(SW.SelectData)null,false);}catch{}
     if(ok){sel++;selNames.Add(nm);}
    }
    r["selected_for_delete"]=selNames.ToArray();
    bool del=false; string how="";
    if(sel>0){
     try{del=assy.DeleteSelections(0);how="DeleteSelections(0)";}catch(Exception ex){how="DeleteSelections threw: "+ex.Message;}
     if(!del){
      try{doc.EditDelete();del=true;how="EditDelete";}catch(Exception ex){how=how+" | EditDelete threw: "+ex.Message;}
     }
    }
    r["delete_ok"]=del; r["delete_how"]=how;
    r["after_delete_components"]=Comps(assy);
    r["after_delete_rebuild_ok"]=doc.ForceRebuild3(false);
    r["after_delete_mates"]=MateErrors(doc);
   }
   r["final_components"]=Comps(assy);
   r["final_mates"]=MateErrors(doc);
   r["saved"]=false;
  }
  finally{
   try{app.CloseDoc(doc.GetTitle());}catch{}
  }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$asm=Get-ChildItem -LiteralPath $dst -Filter '*.SLDASM' -File | Select-Object -First 1 -ExpandProperty FullName
if(-not $asm){ throw "no assembly found in $dst" }
$result=[CapRemovalTest]::Run($asm,[bool]$DeleteCaps)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
