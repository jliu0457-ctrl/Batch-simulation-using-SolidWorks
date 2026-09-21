param(
    [Parameter(Mandatory=$true)][string]$DocPath,
    [Parameter(Mandatory=$true)][string]$GlobalsJson,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$NoSave
)
# Sets named global variables in one part or assembly by rewriting the matching equation
# rows, rebuilds, and saves unless -NoSave is given.  GlobalsJson is a UTF-8 JSON object,
# e.g. {"c":"32","ds":"45"}.
# ASCII only; the JSON is read as UTF-8 from a file so no non-ASCII reaches this script.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "SolidWorks interop missing: $interop" }
$pairsJson=[IO.File]::ReadAllText([IO.Path]::GetFullPath($GlobalsJson),[Text.UTF8Encoding]::new($false))
$source=@'
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class SetGlobals {
 static string S(Func<string> f){try{return f();}catch{return "";}}

 public static object Run(string docPath,string pairsJson,bool save){
  docPath=Path.GetFullPath(docPath);
  // {"c":"32","ds":"45"} -> list of [name,value]
  var pairs=new List<string[]>();
  string body=pairsJson.Trim();
  if(body.StartsWith("{")) body=body.Substring(1);
  if(body.EndsWith("}")) body=body.Substring(0,body.Length-1);
  foreach(string chunk in body.Split(',')){
   string[] kv=chunk.Split(':');
   if(kv.Length<2) continue;
   string k=kv[0].Trim().Trim('"');
   string v=kv[1].Trim().Trim('"');
   if(k.Length>0) pairs.Add(new[]{k,v});
  }
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int e1=0,w1=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(docPath,1,1,"",ref e1,ref w1);
  if(doc==null) doc=(SW.IModelDoc2)app.OpenDoc6(docPath,2,1,"",ref e1,ref w1);
  if(doc==null) throw new Exception("open failed: errors="+e1+" warnings="+w1);
  var r=new Dictionary<string,object>();
  r["document"]=doc.GetPathName();
  try{
   var em=(SW.IEquationMgr)doc.GetEquationMgr();
   if(em==null) throw new Exception("no equation manager");
   int n=em.GetCount();
   var rows=new List<object>();
   foreach(var kv in pairs){
    string name=kv[0], val=kv[1];
    string want="\""+name+"\"";
    bool found=false;
    for(int i=0;i<n;i++){
     string eq=""; try{ eq=em.get_Equation(i); }catch{}
     if(eq.TrimStart().StartsWith(want,StringComparison.Ordinal)){
      string newEq=want+" = "+val;
      string err="";
      try{ em.set_Equation(i,newEq); }catch(Exception ex){ err=ex.Message; }
      string after=""; try{ after=em.get_Equation(i); }catch{}
      rows.Add(new{name=name,index=i,wrote=newEq,now=after,error=err});
      found=true; break;
     }
    }
    if(!found) rows.Add(new{name=name,found=false});
   }
   r["sets"]=rows.ToArray();
   r["rebuild_ok"]=doc.ForceRebuild3(false);
   if(save){ try{ doc.Save(); r["saved"]=true; }catch(Exception ex){ r["saved"]=false; r["save_error"]=ex.Message; } }
   else r["saved"]=false;
  }catch(Exception ex){ r["error"]=ex.ToString(); }
  finally{ try{ app.CloseDoc(doc.GetTitle()); }catch{} }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[SetGlobals]::Run([IO.Path]::GetFullPath($DocPath),$pairsJson,-not $NoSave)
$json=$result | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
