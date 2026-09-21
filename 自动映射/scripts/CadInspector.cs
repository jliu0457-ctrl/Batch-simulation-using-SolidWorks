
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;
public static class CadInspector {
 static Dictionary<string,object> Obj(){return new Dictionary<string,object>();}
 static object[] Arr(object o){return o as object[] ?? new object[0];}
 public static Dictionary<string,object> Run(string root) {
  var r=Obj();var docs=new List<object>();var errors=new List<string>();
  r["documents"]=docs;r["errors"]=errors;r["stage"]="connect";
  SW.ISldWorks app=null;
  try {
    object com=null;
    try { com=Marshal.GetActiveObject("SldWorks.Application.34"); }
    catch { com=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true)); }
    app=(SW.ISldWorks)com;
    r["revision"]=app.RevisionNumber();
    r["stage"]="read";
    if(!((string)r["revision"]).StartsWith("34."))throw new Exception("Unexpected version");
    var folder=Path.Combine(root,"working","baseline");
    var paths=new List<string>();
    paths.AddRange(Directory.GetFiles(folder,"*.SLDPRT"));
    paths.AddRange(Directory.GetFiles(folder,"*.SLDASM"));
    foreach(var path in paths) {
      var d=Obj();d["file"]=Path.GetFileName(path);var de=new List<string>();d["errors"]=de;docs.Add(d);
      try{
        int err=0,warn=0;int type=path.EndsWith(".SLDASM",StringComparison.OrdinalIgnoreCase)?2:1;
        SW.IModelDoc2 doc=(SW.IModelDoc2)app.OpenDoc6(path,type,3,"",ref err,ref warn);
        d["open_errors"]=err;d["open_warnings"]=warn;
        if(doc==null){de.Add("OpenDoc6 returned null");continue;}
        d["path"]=doc.GetPathName();d["title"]=doc.GetTitle();d["dirty"]=doc.GetSaveFlag();
        d["configurations"]=doc.GetConfigurationNames();
        try{d["active_config"]=doc.ConfigurationManager.ActiveConfiguration.Name;}catch(Exception ex){de.Add("config:"+ex.Message);}
        var equations=new List<object>();d["equations"]=equations;
        try{SW.IEquationMgr eq=(SW.IEquationMgr)doc.GetEquationMgr();
          if(eq!=null){for(int i=0;i<eq.GetCount();i++){var e=Obj();e["index"]=i;e["text"]=eq.get_Equation(i);e["global"]=eq.get_GlobalVariable(i);equations.Add(e);}}
        }catch(Exception ex){de.Add("equations:"+ex.Message);}
        var features=new List<object>();d["features"]=features;
        SW.IFeature feat=(SW.IFeature)doc.FirstFeature();int cnt=0;
        while(feat!=null && cnt++<1000){
          var f=Obj();f["name"]=feat.Name;f["type"]=feat.GetTypeName2();features.Add(f);
          var dims=new List<object>();f["dimensions"]=dims;
          try{
            object dd=feat.GetFirstDisplayDimension();int n=0;
            while(dd!=null&&n++<200){var disp=(SW.IDisplayDimension)dd;var dim=(SW.IDimension)disp.GetDimension2(0);
              if(dim!=null){var dm=Obj();dm["full_name"]=dim.FullName;dm["value_SI"]=dim.SystemValue;dm["readonly"]=dim.ReadOnly;dims.Add(dm);}
              dd=feat.GetNextDisplayDimension(dd);
            }
          }catch(Exception ex){f["error"]=ex.Message;}
          feat=(SW.IFeature)feat.GetNextFeature();
        }
        if(type==2){var comps=new List<object>();d["components"]=comps;var assy=(SW.IAssemblyDoc)doc;
          foreach(object x in Arr(assy.GetComponents(false))){var co=(SW.IComponent2)x;var cr=Obj();cr["name"]=co.Name2;cr["path"]=co.GetPathName();cr["config"]=co.ReferencedConfiguration;cr["suppression"]=co.GetSuppression();comps.Add(cr);}
        }
      }catch(Exception ex){de.Add(ex.ToString());}
    }
    r["stage"]="inventory_finished";
  }catch(Exception ex){errors.Add(ex.ToString());r["stage"]="failed";}
  return r;
 }
}
