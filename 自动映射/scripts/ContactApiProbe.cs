using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using SW=SolidWorks.Interop.sldworks;
using CW=SolidWorks.Interop.cosworks;
public static class ContactApiProbe {
 static Dictionary<string,object> O(){return new Dictionary<string,object>();}
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string Hash(string p){using(var s=File.OpenRead(p))using(var h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(s)).Replace("-","").ToLowerInvariant();}
 static string Inside(string p,string root){p=Path.GetFullPath(p);if(!p.StartsWith(Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new Exception("outside task root");return p;}
 static void Error(Dictionary<string,object> obj,string stage,Exception ex){obj[stage+"_error"]=ex.GetType().FullName+": "+ex.Message;}
 static Dictionary<string,object> Material(string name,string db){
   var r=O();r["material_name"]=name;r["database_returned"]=db;
   r["database_path_exists"]=!String.IsNullOrEmpty(db)&&File.Exists(db);
   r["mechanical_values_read"]=false;return r;
 }
 static CW.ICosmosWorks ConnectSimulation(SW.ISldWorks app,List<object> probes){
  foreach(string prog in new[]{"SldWorks.Simulation.19","SldWorks.Simulation"}){
   var p=O();p["prog_id"]=prog;probes.Add(p);
   try{
    object addin=app.GetAddInObject(prog);p["returned_null"]=addin==null;
    if(addin==null)continue;
    CW.ICosmosWorks cosmos=null;
    try{cosmos=(CW.ICosmosWorks)((CW.ICwAddincallback)addin).CosmosWorks;p["interface"]="ICwAddincallback.CosmosWorks";}
    catch(InvalidCastException){cosmos=(CW.ICosmosWorks)addin;p["interface"]="ICosmosWorks";}
    if(cosmos!=null)return cosmos;
   }catch(Exception ex){Error(p,"connection",ex);}
  }
  return null;
 }
 static string LoadCode(int code){
  switch(code){case 0:return "swSuccess";case 1:return "swAddinNotLoaded";case 2:return "swAddinAlreadyLoaded";
   case 3:return "swFileNotFound";case 4:return "swAddinsDisabled";case 5:return "swLoadConflict";
   case 6:return "swRegistrationError";case 7:return "swLicenseError";default:return "swUnknownError";}
 }
 public static Dictionary<string,object> Run(string root){return Run(root,false);}
 public static Dictionary<string,object> Run(string root,bool loadInstalledSimulation){
  root=Path.GetFullPath(root);string folder=Inside(Path.Combine(root,"working","assembly_repair_v4"),root);
  var r=O();var docs=new List<object>();var errors=new List<string>();
  r["documents"]=docs;r["errors"]=errors;r["mode"]="read_only_material_and_existing_study_inventory";
  r["load_installed_simulation_requested"]=loadInstalledSimulation;
  r["created_studies"]=false;r["changed_materials"]=false;r["started_mesh_or_solver"]=false;
  r["started_utc"]=DateTime.UtcNow.ToString("o");r["stage"]="attach_existing_sw34";
  SW.ISldWorks app=null;SW.IModelDoc2 original=null;
  try{
   try{app=(SW.ISldWorks)Marshal.GetActiveObject("SldWorks.Application.34");r["connection"]="rot";}catch(COMException){app=(SW.ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true));r["connection"]="registered_34_activator_fallback";}
   r["revision"]=app.RevisionNumber();
   if(!((string)r["revision"]).StartsWith("34."))throw new Exception("Unexpected SolidWorks revision");
   original=(SW.IModelDoc2)app.ActiveDoc;
   try{r["material_database_search_paths"]=app.GetMaterialDatabases();}catch(Exception ex){Error(r,"material_databases",ex);}
   var paths=new List<string>(Directory.GetFiles(folder,"*.SLDPRT"));
   paths.AddRange(Directory.GetFiles(folder,"*.SLDASM"));
   foreach(var path0 in paths){
    string path=Inside(path0,root);var d=O();docs.Add(d);d["path"]=path;d["file"]=Path.GetFileName(path);d["sha256_before"]=Hash(path);
    SW.IModelDoc2 doc=null;bool opened=false;string initialConfig=null;
    try{
     bool assembly=path.EndsWith(".SLDASM",StringComparison.OrdinalIgnoreCase);
     doc=(SW.IModelDoc2)app.GetOpenDocumentByName(path);d["already_open"]=doc!=null;
     int err=0,warn=0;
     if(doc==null){doc=(SW.IModelDoc2)app.OpenDoc6(path,assembly?2:1,3,"",ref err,ref warn);opened=doc!=null;}
     d["open_errors"]=err;d["open_warnings"]=warn;d["requested_open_flags"]="silent + read_only";
     if(doc==null)throw new Exception("OpenDoc6 returned null");
     if(!String.Equals(Path.GetFullPath(doc.GetPathName()),path,StringComparison.OrdinalIgnoreCase))throw new Exception("Opened wrong document path");
     d["dirty_before"]=doc.GetSaveFlag();
     initialConfig=doc.ConfigurationManager.ActiveConfiguration.Name;
     var configs=new List<object>();d["configurations"]=configs;
     foreach(object cfgObj in A(doc.GetConfigurationNames())){
      string cfg=(string)cfgObj;var c=O();configs.Add(c);c["name"]=cfg;
      bool shown=doc.ShowConfiguration2(cfg);c["show_configuration_return"]=shown;
      // ShowConfiguration2 may return false when the requested config is already active.
      // Reacquire the document and bodies AFTER every configuration switch.
      doc=(SW.IModelDoc2)app.GetOpenDocumentByName(path);
      if(doc==null)throw new Exception("Document unavailable after configuration switch");
      string actualConfig=doc.ConfigurationManager.ActiveConfiguration.Name;c["active_config_after"]=actualConfig;
      if(!String.Equals(actualConfig,cfg,StringComparison.Ordinal)){c["configuration_error"]="requested configuration not active";continue;}
      if(!assembly){
       SW.IPartDoc part=(SW.IPartDoc)doc;string db="";
       try{c["part_material"]=Material(part.GetMaterialPropertyName2(cfg,out db),db);}catch(Exception ex){Error(c,"part_material",ex);}
       var bodies=new List<object>();c["bodies"]=bodies;
       foreach(object bo in A(part.GetBodies2(-1,false))){
        SW.IBody2 body=(SW.IBody2)bo;var b=O();b["name"]=body.Name;bodies.Add(b);string bdb="";
        try{b["material"]=Material(body.GetMaterialPropertyName(cfg,out bdb),bdb);}catch(Exception ex){Error(b,"body_material",ex);}
       }
      }
     }
     if(assembly){
      int actError=0;
      app.ActivateDoc3(path,false,1,ref actError);d["activation_error"]=actError;
      var sim=O();d["simulation"]=sim;sim["status"]="not_connected";
      var probes=new List<object>();sim["addin_attempts"]=probes;
      CW.ICosmosWorks cosmos=ConnectSimulation(app,probes);
      sim["load_installed_requested"]=loadInstalledSimulation;
      if(cosmos==null && loadInstalledSimulation){
       var load=O();sim["load_installed_attempt"]=load;
       string installed=@"D:\solidworks2026\SW\SOLIDWORKS\Simulation\cosworks.dll";
       load["dll_path"]=installed;load["dll_exists"]=File.Exists(installed);
       try{
        if(!File.Exists(installed))throw new Exception("Installed Simulation DLL not found; no installation attempted");
        string version=System.Diagnostics.FileVersionInfo.GetVersionInfo(installed).FileVersion;
        load["file_version"]=version;load["sha256"]=Hash(installed);
        if(!version.StartsWith("34."))throw new Exception("Refusing non-2026 Simulation DLL");
        int result=app.LoadAddIn(installed);
        load["return_code"]=result;load["return_name"]=LoadCode(result);
        load["installation_performed"]=false;load["solver_started"]=false;
        if(result==0||result==2)cosmos=ConnectSimulation(app,probes);
       }catch(Exception ex){Error(load,"load",ex);}
      }
      if(cosmos!=null){
       SW.IModelDoc2 activeCheck=(SW.IModelDoc2)app.ActiveDoc;
       if(activeCheck==null || !String.Equals(Path.GetFullPath(activeCheck.GetPathName()),path,StringComparison.OrdinalIgnoreCase))throw new Exception("Active CAD document does not match authorized assembly before study inventory");
       sim["active_cad_path"]=activeCheck.GetPathName();
       CW.ICWModelDoc cwDoc=(CW.ICWModelDoc)cosmos.ActiveDoc;
       if(cwDoc==null){sim["status"]="connected_no_active_simulation_document";}
       else{
        CW.ICWStudyManager mgr=(CW.ICWStudyManager)cwDoc.StudyManager;
        if(mgr==null){sim["status"]="connected_no_study_manager";}
        else{
         sim["status"]="existing_studies_read";sim["study_count"]=mgr.StudyCount;
         var studies=new List<object>();sim["studies"]=studies;
         for(int i=0;i<mgr.StudyCount;i++){
          CW.ICWStudy s=(CW.ICWStudy)mgr.GetStudy(i);var st=O();st["index"]=i;
          st["name"]=s.Name;st["analysis_type"]=s.AnalysisType;st["analysis_type_name"]=Enum.GetName(typeof(CW.swsAnalysisStudyType_e),s.AnalysisType);
          st["configuration"]=s.ConfigurationName;st["mesh_type"]=s.MeshType;studies.Add(st);
         }
        }
       }
      }
     }
    }catch(Exception ex){Error(d,"read",ex);}
    finally{
     if(doc!=null){
      try{if(initialConfig!=null)doc.ShowConfiguration2(initialConfig);d["dirty_after"]=doc.GetSaveFlag();}catch(Exception ex){Error(d,"restore_configuration",ex);}
      if(opened){try{app.CloseDoc(doc.GetTitle());d["closed_probe_opened_doc"]=true;}catch(Exception ex){Error(d,"close",ex);}}
     }
     d["sha256_after"]=Hash(path);d["file_unchanged"]=Object.Equals(d["sha256_before"],d["sha256_after"]);
    }
   }
   r["stage"]="read_completed";
  }catch(Exception ex){errors.Add(ex.ToString());r["stage"]="failed";}
  finally{
   if(app!=null&&original!=null){try{int e=0;app.ActivateDoc3(original.GetPathName(),false,1,ref e);r["original_document_reactivation_error"]=e;}catch(Exception ex){Error(r,"reactivate",ex);}}
   r["ended_utc"]=DateTime.UtcNow.ToString("o");
  }
  return r;
 }
}
