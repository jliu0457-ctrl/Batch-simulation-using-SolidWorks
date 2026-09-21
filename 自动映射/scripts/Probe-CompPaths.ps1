param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Read-only.  Opens the assembly and lists, for every component, the absolute path of the part
# file SolidWorks actually loaded, plus whether that file exists and its last-write time.
#
# Why: scratch copies were made with cp -r.  If a copy's assembly stores ABSOLUTE references
# back into assembly_batch_v7 then opening the copy silently loads the ORIGINAL parts, and any
# measurement taken on the copy is meaningless.  This has to be checked before trusting one.
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

public static class CompPaths {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static string CP(params int[] cps){ var s=""; foreach(int c in cps) s+=((char)c).ToString(); return s; }

 public static object Run(string assemblyPath){
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
   var rows=new List<object>();
   var assy=(SW.IAssemblyDoc)doc;
   foreach(var o in A(assy.GetComponents(false))){
    var c=o as SW.IComponent2; if(c==null) continue;
    string name=S(()=>c.Name2);
    bool virtualComp=false; try{ virtualComp=c.IsVirtual; }catch{}
    string path=""; try{ path=c.GetPathName(); }catch{}
    string written=""; long size=0; bool exists=false;
    if(!virtualComp && path.Length>0){
     exists=File.Exists(path);
     if(exists){ try{ written=File.GetLastWriteTime(path).ToString("MM-dd HH:mm:ss"); size=new FileInfo(path).Length; }catch{} }
    }
    // modeller doc: what is actually loaded right now
    string loaded="";
    try{ var pd=c.GetModelDoc2() as SW.IModelDoc2; if(pd!=null) loaded=S(()=>pd.GetPathName()); }catch{}
    rows.Add(new Dictionary<string,object>{{"component",name},{"virtual",virtualComp?"1":"0"},
      {"referenced_path",path},{"loaded_doc_path",loaded},
      {"exists",exists?"1":"0"},{"mtime",written},{"bytes",size}});
   }
   r["assembly"]=assemblyPath;
   r["components"]=rows.ToArray();
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
$result=[CompPaths]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
