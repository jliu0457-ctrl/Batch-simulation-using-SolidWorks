param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$Apply
)
# Removes the four 封盖 components from an assembly and, with -Apply, saves the assembly and
# deletes the 封盖*.SLDPRT files from the same folder.
#
# Why: the caps exist in the template only to be deleted again by the adapter.  Parameterizing
# with them in place leaves the pipe unsealed, so every run deletes them and Create Lids builds
# fresh ones.  A template without caps removes that whole step - and with it the class of
# failure where an inherited Flow project referenced the deleted caps and raised a modal dialog.
#
# Saves with Save3 (silent).  Plain Save() on an assembly raises the modal
# "必须保存零部件文档" whenever a component is dirty, and answering it saves every open document -
# which is how an earlier run re-saved a whole template by accident.
#
# ASCII only - Chinese is built from code points inside the C#.
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

public static class CapRemover {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static string CP(params int[] cps){ var s=""; foreach(int c in cps) s+=((char)c).ToString(); return s; }
 static string CapPrefix(){ return CP(0x5c01,0x76d6); }   // 封盖

 public static object Run(string assemblyPath,bool apply){
  string config=CP(0x5f00,0x5ea6)+"45"+CP(0xb0);
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,config,ref e,ref w);
  if(doc==null){ r["error"]="open failed errors="+e+" warnings="+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   var assy=(SW.IAssemblyDoc)doc;
   var caps=A(assy.GetComponents(false)).Cast<SW.IComponent2>()
            .Where(c=>S(()=>c.Name2).StartsWith(CapPrefix(),StringComparison.Ordinal)).ToArray();
   r["caps_found"]=caps.Select(c=>S(()=>c.Name2)).ToArray();
   r["component_count_before"]=A(assy.GetComponents(false)).Length;
   if(caps.Length==0){ r["note"]="no caps present"; return r; }
   if(!apply){ r["would_remove"]=caps.Length; return r; }

   doc.ClearSelection2(true);
   bool append=false;
   foreach(var cap in caps){
    if(!cap.Select4(append,null,false)) throw new InvalidOperationException("Could not select cap: "+S(()=>cap.Name2));
    append=true;
   }
   if(!assy.DeleteSelections(0)) throw new InvalidOperationException("SolidWorks could not delete the cap components.");
   doc.ClearSelection2(true);
   doc.ForceRebuild3(false);
   var left=A(assy.GetComponents(false)).Cast<SW.IComponent2>()
             .Where(c=>S(()=>c.Name2).StartsWith(CapPrefix(),StringComparison.Ordinal)).ToArray();
   r["caps_remaining"]=left.Length;
   if(left.Length!=0) throw new InvalidOperationException("A cap component remained.");
   r["component_count_after"]=A(assy.GetComponents(false)).Length;
   var errs=new List<object>();
   var f=(SW.IFeature)doc.FirstFeature();
   while(f!=null){ if(f.GetErrorCode()!=0) errs.Add(new{name=S(()=>f.Name),code=f.GetErrorCode()}); f=(SW.IFeature)f.GetNextFeature(); }
   r["feature_errors"]=errs.ToArray();
   int se=0,sw=0;
   r["save_ok"]=doc.Save3(1,ref se,ref sw);   // silent: never raises the save-components prompt
   r["save_errors"]=se;
   var removed=new List<string>();
   string folder=Path.GetDirectoryName(assemblyPath);
   for(int i=1;i<=4;i++){
    string p=Path.Combine(folder,CapPrefix()+i+".SLDPRT");
    if(File.Exists(p)){ File.Delete(p); removed.Add(Path.GetFileName(p)); }
   }
   r["cap_files_deleted"]=removed.ToArray();
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
$result=[CapRemover]::Run([IO.Path]::GetFullPath($AssemblyPath),[bool]$Apply)
$json=$result | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
