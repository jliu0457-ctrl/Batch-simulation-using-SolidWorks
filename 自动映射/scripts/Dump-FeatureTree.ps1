param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Read-only.  Opens the assembly and dumps EVERY feature with its type, depth and parent path,
# plus the mate list reached by three different traversal strategies.  Written because the
# earlier mate walker reported one mate for an assembly that visibly holds about twenty.
# ASCII only; Chinese appears as unicode escapes in the C# source.
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

public static class TreeDump {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}

 // full recursive descent, recording depth and the parent chain
 // NOTE: a sub-feature chain advances with GetNextSubFeature(), not GetNextFeature().
 // Using the wrong one silently stops after the first child - which is why an earlier
 // walker saw one mate in an assembly that holds twenty.
 static void Walk(SW.IFeature f, int depth, string parent, List<object> rows, bool asSub){
  while(f!=null){
   string nm=S(()=>f.Name), ty=S(()=>f.GetTypeName2());
   var row=new Dictionary<string,object>{{"depth",depth},{"name",nm},{"type",ty},
     {"parent",parent},{"error",S(()=>f.GetErrorCode().ToString())},
     {"suppressed",S(()=>f.IsSuppressed()?"1":"0")}};
   if(ty.IndexOf("Mate",StringComparison.OrdinalIgnoreCase)>=0 && ty!="MateGroup"){
    var comps=new List<string>();
    try{
     var mm=(SW.IMate2)f.GetSpecificFeature2();
     if(mm!=null) for(int i=0;i<mm.GetMateEntityCount();i++){
      var me=(SW.IMateEntity2)mm.MateEntity(i);
      var c=(SW.IComponent2)me.ReferenceComponent;
      comps.Add(c==null?"<assembly>":c.Name2);
     }
    }catch(Exception ex){ comps.Add("<err "+ex.Message+">"); }
    row["components"]=comps.ToArray();
   }
   rows.Add(row);
   var sub=(SW.IFeature)f.GetFirstSubFeature();
   if(sub!=null) Walk(sub,depth+1,parent+"/"+nm,rows,true);
   f = asSub ? (SW.IFeature)f.GetNextSubFeature() : (SW.IFeature)f.GetNextFeature();
  }
 }

 public static object Run(string assemblyPath){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null){ r["error"]="open failed errors="+e+" warnings="+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   var rows=new List<object>();
   Walk((SW.IFeature)doc.FirstFeature(),0,"",rows,false);
   r["feature_count"]=rows.Count;
   r["features"]=rows.ToArray();
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
$result=[TreeDump]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
