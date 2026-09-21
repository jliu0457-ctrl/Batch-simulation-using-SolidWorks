using System;
using System.IO;
using System.Linq;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using SW = SolidWorks.Interop.sldworks;

/// <summary>
/// Seven-variable CAD adapter for the audited valve template.
/// Geometry only: this class never runs a solver or emits training labels.
/// Dmax is the maximum caliper diameter of the disc solid projected onto its
/// closed flow-normal plane; it is not the cone construction circle.
/// </summary>
public static class SevenVariableAdapter
{
    const double RadialDifferenceMm = 1.575;
    // 09密封圈's stock must always reach past the seal cone, otherwise the revolved cut misses the
    // outer edge entirely and no cone is cut.  D10@草图1 is the rectangle's radial span (outer edge
    // minus the Ø140 bore), and that outer edge lies wholly INSIDE the revolved cut - verified on
    // the V6 master: raising D10 by 20 mm leaves volume 89696.263, the bounding box and the cone
    // face (R105.2048 ha17.75 area4648.23) bit-identical.  So writing it is free, and it removes
    // the upper bound on s that the fixed outer edge used to impose.
    const double RingOuterMarginMm = 12.0;   // stock kept beyond the seal cone
    const double RingBoreRadiusMm = 70.0;    // from D1@草图1 = 140 (inner bore diameter)
    // 10压板's stock rectangle, same idea as the ring's: its outer edge lies wholly inside the
    // revolved cut (verified - raising D13 by 10 mm left volume 224076.89, the bounding box and
    // both cone faces bit-identical), so growing it is free and keeps the cut reaching past the
    // seal cone.  47.5 is the rectangle's inner edge on the template.
    const double PlateOuterMarginMm = 13.0;
    const double PlateStockInnerEdgeMm = 47.5;
    // D6@草图1 minus c on the audited master: 44 - 32.  D6 is the distance from the disc's far
    // axial face to the valve-stem centre, so keeping it at c + this offset makes that face travel
    // with c instead of staying pinned.  D2@草图1 (which is c) only slides the sealing cone, and on
    // its own it shears the part: the gasket - held by its mate to the disc - then misses the seat
    // ring by a full millimetre (measured 11572 mm3 of ring interference at c = 31).  At the
    // template's c = 32 this evaluates to 44, the value already stored in the master, so the
    // template file itself needs no edit and its manifest hash stays valid.
    const double DiscFarFaceToStemOffsetMm = 12.0;
    const double DimensionToleranceSI = 1e-8;
    const double GeometryToleranceMm = 0.001;
    // Dmax acceptance band.  Was 0.001 mm (about 5 ppm on a 189 mm disc), which cost real
    // time: on the 2026-09-19 run the secant reached a 0.000781 mm midpoint residual in two
    // iterations, missed the band by 0.0002 mm, and then spent EIGHT more iterations
    // (about 19.5 s each) chasing it - 3.6 of the run's 5.25 minutes.  0.005 mm is 26 ppm,
    // i.e. still two orders of magnitude finer than anything a CFD solution can resolve.
    const double DiameterToleranceMm = 0.005;
    const double CaliperBandMm = 0.001;
    const double AxisToleranceMm = 0.001;
    const double AngleToleranceDeg = 0.00001;
    const string AssemblyConfiguration = "开度45°";

    public sealed class Design
    {
        public double c_mm, e_mm, phi_deg, alpha_deg, Dmax_mm, bm_mm, ds_mm;
        public Design Copy() { return (Design)MemberwiseClone(); }
        public void Validate()
        {
            double[] a = { c_mm, e_mm, phi_deg, alpha_deg, Dmax_mm, bm_mm, ds_mm };
            if (a.Any(x => Double.IsNaN(x) || Double.IsInfinity(x))) throw new ArgumentException("All seven inputs must be finite.");
            if (c_mm < 0 || e_mm < 0 || Dmax_mm <= 0 || bm_mm <= 0 || ds_mm <= 0) throw new ArgumentException("Invalid nonpositive geometry input.");
            if (bm_mm >= Dmax_mm || ds_mm >= Dmax_mm) throw new ArgumentException("Seal thickness and shaft diameter must be smaller than Dmax.");
            if (phi_deg <= 0 || alpha_deg <= 0 || alpha_deg >= 180 || alpha_deg / 2 + phi_deg >= 90)
                throw new ArgumentException("Inputs are outside the audited two-angle template domain.");
        }
    }

    public sealed class CaliperSample
    {
        public double angle_deg, width_mm;
    }

    public sealed class CaliperResult
    {
        public double lower_mm, upper_mm, midpoint_mm, band_mm, step_deg, peak_angle_deg;
        public int directions, support_calls;
        public double[] peak_plus_point_m, peak_minus_point_m;
        public string plane = "disc local yz; verified parallel to closed flow-normal plane";
        public string bound = "W <= exact maximum caliper <= W / cos(step_rad / 2), subject to CAD kernel support precision";
        public List<CaliperSample> samples = new List<CaliperSample>();
    }

    public sealed class WriteRecord
    {
        public string file, parameter, variable;
        public double before_SI, requested_SI, after_SI;
        public int set_status;
    }

    public sealed class Context : IDisposable
    {
        static readonly string[] ParameterizedPartTokens = { "03阀体", "04阀轴", "08大垫片", "09密封圈", "10压板", "11蝶板" };
        public readonly SW.ISldWorks App;
        public readonly string Root, Folder;
        public readonly Dictionary<string, SW.IModelDoc2> Parts = new Dictionary<string, SW.IModelDoc2>(StringComparer.OrdinalIgnoreCase);
        public readonly List<WriteRecord> Writes = new List<WriteRecord>();
        public readonly List<object> SolveTrace = new List<object>();
        public SW.IModelDoc2 Assembly;
        public Context(SW.ISldWorks app, string root, string folder)
        {
            App = app; Root = root; Folder = Under(folder, Path.Combine(root, "working", "seven_variable_trials"), true);
            RefuseSameNamedOpenDocuments(app, Directory.GetFiles(Folder, "*.SLD*"));
            try
            {
                App.DocumentVisible(false, 1);
                foreach (string path in Directory.GetFiles(Folder, "*.SLDPRT")
                    .Where(p => !Path.GetFileName(p).StartsWith("~$", StringComparison.OrdinalIgnoreCase))
                    .Where(p => ParameterizedPartTokens.Any(token => Path.GetFileName(p).Contains(token))))
                {
                    Stage(this, "open_part_start", Path.GetFileName(path));
                    Parts.Add(Path.GetFileName(path), Open(app, path, 1, false, ""));
                    Stage(this, "open_part_complete", Path.GetFileName(path));
                }
                if (Parts.Count != 6) throw new InvalidOperationException("Expected the six parameterized core parts.");
                // The template carries no caps as of 2026-09-19.  The four caps used to exist only
                // to be deleted on every run, and with them came a failure mode: an inherited Flow
                // project whose boundary conditions referenced the deleted caps raised a modal
                // dialog that blocked the whole run.  Six parts is now the entire template, and
                // Flow's Create Lids builds the four lids on the capless output instead.
                int partFiles = Directory.GetFiles(Folder, "*.SLDPRT")
                    .Count(p => !Path.GetFileName(p).StartsWith("~$", StringComparison.OrdinalIgnoreCase));
                if (partFiles != 6)
                    throw new InvalidOperationException("Expected exactly six external parts; found " + partFiles.ToString(CultureInfo.InvariantCulture));
            }
            catch { CloseFolder(app, Folder); throw; }
            finally { App.DocumentVisible(true, 1); }
        }
        public SW.IModelDoc2 Part(string token) { return Parts.Single(x => x.Key.Contains(token)).Value; }
        public string FileName(string token) { return Parts.Single(x => x.Key.Contains(token)).Key; }
        public void Dispose() { CloseFolder(App, Folder); }
    }

    static Dictionary<string, object> O() { return new Dictionary<string, object>(); }
    static object[] A(object value) { return value as object[] ?? new object[0]; }
    static double[] D(object value) { return value as double[] ?? new double[0]; }
    static double Radians(double degrees) { return degrees * Math.PI / 180; }
    static double Degrees(double radians) { return radians * 180 / Math.PI; }
    static double Dot(double[] a, double[] b) { return a.Zip(b, (x, y) => x * y).Sum(); }
    static double[] Sub(double[] a, double[] b) { return a.Zip(b, (x, y) => x - y).ToArray(); }
    static double[] Cross(double[] a, double[] b) { return new[] { a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0] }; }
    static double Norm(double[] a) { return Math.Sqrt(Dot(a, a)); }
    static double[] Unit(double[] a) { double n = Norm(a); if (n <= 0 || Double.IsNaN(n)) throw new InvalidOperationException("Invalid axis."); return a.Select(x => x / n).ToArray(); }
    static string Full(string p) { return Path.GetFullPath(p).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar); }
    static string Under(string path, string parent, bool mustExist)
    {
        string p = Full(path), b = Full(parent);
        if (!p.StartsWith(b + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Path is outside its authorized subtree: " + p);
        string cursor = p;
        while (cursor.Length >= b.Length)
        {
            if ((File.Exists(cursor) || Directory.Exists(cursor)) && (File.GetAttributes(cursor) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("Reparse points are not allowed in CAD task paths.");
            if (String.Equals(cursor, b, StringComparison.OrdinalIgnoreCase)) break;
            cursor = Path.GetDirectoryName(cursor);
            if (String.IsNullOrEmpty(cursor)) break;
        }
        if (mustExist && !Directory.Exists(p) && !File.Exists(p)) throw new FileNotFoundException(p);
        return p;
    }
    static string RootPath(string root)
    {
        string r = Full(root);
        string workspace = Path.GetDirectoryName(r);
        if (String.IsNullOrEmpty(workspace)) throw new InvalidOperationException("SolidWorks task root must have a workspace parent.");
        r = Under(r, workspace, true);
        if (!String.Equals(Path.GetFileName(r), "自动映射", StringComparison.Ordinal)) throw new InvalidOperationException("Expected the authorized 自动映射 task root.");
        return r;
    }
    static string Hash(string p)
    {
        // SolidWorks may keep referenced component files open while the assembly
        // is active. Hash read-only bytes without demanding an exclusive handle.
        using (var stream = new FileStream(p, FileMode.Open, FileAccess.Read,
                                           FileShare.ReadWrite | FileShare.Delete))
        using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
    static JavaScriptSerializer Serializer()
    {
        var js = new JavaScriptSerializer(); js.MaxJsonLength = 100000000; js.RecursionLimit = 100; return js;
    }
    static void WriteJson(string path, object data) { File.WriteAllText(path, Serializer().Serialize(data), new System.Text.UTF8Encoding(false)); }

    static readonly List<string> ProgressWriteWarnings = new List<string>();
    static void WriteStage(string folder, string stage, object details)
    {
        // At most one write per document, rebuild, evaluation, angle or save step;
        // never one per support query. Each isolated directory has its own file.
        string path = Path.Combine(folder, "mapping_progress.json");
        string temporary = path + ".tmp";
        try
        {
            WriteJson(temporary, new { utc = DateTime.UtcNow.ToString("o"), stage = stage, details = details });
            if (File.Exists(path)) File.Replace(temporary, path, null); else File.Move(temporary, path);
        }
        catch (IOException ex)
        {
            // A progress reader can briefly hold the destination. This must not
            // invalidate CAD work; the final authoritative report is separate.
            if (ProgressWriteWarnings.Count < 100) ProgressWriteWarnings.Add(stage + ": " + ex.Message);
        }
    }
    static void Stage(Context c, string stage, object details)
    {
        WriteStage(c.Folder, stage, details);
    }

    static SW.ISldWorks Connect()
    {
        object com = null;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (COMException)
        {
            try { com = Marshal.GetActiveObject("SldWorks.Application"); }
            catch (COMException)
            {
                try { com = Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34", true)); }
                catch (COMException) { com = Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application", true)); }
            }
        }
        var app = (SW.ISldWorks)com;
        if (!app.RevisionNumber().StartsWith("34.", StringComparison.Ordinal)) throw new InvalidOperationException("SolidWorks 2026 is required.");
        return app;
    }
    static void RefuseSameNamedOpenDocuments(SW.ISldWorks app, IEnumerable<string> paths)
    {
        var stems = new HashSet<string>(paths.Select(Path.GetFileNameWithoutExtension), StringComparer.OrdinalIgnoreCase);
        var d = (SW.IModelDoc2)app.GetFirstDocument(); int count = 0;
        while (d != null && count++ < 1000)
        {
            string fileStem = Path.GetFileNameWithoutExtension(d.GetPathName());
            string titleStem = Path.GetFileNameWithoutExtension(d.GetTitle());
            if (stems.Contains(fileStem) || stems.Contains(titleStem))
                throw new InvalidOperationException("A same-named document is already open. No document was closed or modified: " + d.GetPathName());
            d = (SW.IModelDoc2)d.GetNext();
        }
    }
    static void CloseFolder(SW.ISldWorks app, string folder)
    {
        var titles = new List<string>(); var d = (SW.IModelDoc2)app.GetFirstDocument(); int count = 0;
        while (d != null && count++ < 1000)
        {
            if (d.GetPathName().StartsWith(folder + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) titles.Add(d.GetTitle());
            d = (SW.IModelDoc2)d.GetNext();
        }
        foreach (string title in titles) app.CloseDoc(title);
    }
    static SW.IModelDoc2 Open(SW.ISldWorks app, string path, int type, bool readOnly, string configuration)
    {
        int errors = 0, warnings = 0;
        var doc = (SW.IModelDoc2)app.OpenDoc6(path, type, readOnly ? 3 : 1, configuration, ref errors, ref warnings);
        if (doc == null || errors != 0) throw new InvalidOperationException("OpenDoc6 failed: " + path + "; errors=" + errors + "; warnings=" + warnings);
        if (!String.Equals(Full(doc.GetPathName()), Full(path), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("SolidWorks resolved a different file path: " + doc.GetPathName());
        return doc;
    }
    static List<object> FeatureErrors(SW.IModelDoc2 doc)
    {
        var list = new List<object>(); var f = (SW.IFeature)doc.FirstFeature(); int count = 0;
        while (f != null && count++ < 1000)
        {
            if (f.GetErrorCode() != 0) list.Add(new { name = f.Name, code = f.GetErrorCode() });
            var sf = (SW.IFeature)f.GetFirstSubFeature(); int subcount = 0;
            while (sf != null && subcount++ < 500)
            {
                if (sf.GetErrorCode() != 0) list.Add(new { name = sf.Name, code = sf.GetErrorCode() });
                sf = (SW.IFeature)sf.GetNextSubFeature();
            }
            f = (SW.IFeature)f.GetNextFeature();
        }
        return list;
    }
    static SW.IFeature FindFeature(SW.IModelDoc2 doc, string name)
    {
        var f = (SW.IFeature)doc.FirstFeature(); int count = 0;
        while (f != null && count++ < 1000)
        {
            if (String.Equals(f.Name, name, StringComparison.Ordinal)) return f;
            f = (SW.IFeature)f.GetNextFeature();
        }
        return null;
    }
    // The V6 lock mates 锁定1/2/3 tie the disc to the gasket, the seal ring and the
    // pressure plate and over-define the assembly.  While they are active the solver
    // cannot slide the gasket (1.000 mm) and the pressure plate (0.150 mm) into the
    // positions the coincident mates require once any dimension changes, so every
    // rebuild reports "components cannot move to satisfy this mate".  Suppressing them
    // restores a well-defined system; repeated rebuilds then report zero mate errors.
    // Measured 2026-09-17: with the locks active the assembly reports 6 mate errors,
    // with them suppressed 0 errors over three consecutive rebuilds.
    // 锁定4 (disc <-> shaft) is included as of 2026-09-18: it freezes the shaft against the
    // disc, so once `e` moves the disc's bore the shaft cannot follow and the two axes end up
    // exactly delta-e apart (measured 0.300000 mm for e: 3.7 -> 4).  It also over-defines the
    // assembly, which makes AddMate5 for a shaft-to-bore concentric mate fail with
    // swAddMateError_OverDefinedAssembly (5).
    static readonly string[] OverConstrainingLockMates = { "锁定1", "锁定2", "锁定3", "锁定4" };

    static SW.IFeature FindMateFeature(SW.IModelDoc2 doc, string name)
    {
        var f = (SW.IFeature)doc.FirstFeature(); int count = 0;
        while (f != null && count++ < 1000)
        {
            if (String.Equals(f.Name, name, StringComparison.Ordinal)) return f;
            var s = (SW.IFeature)f.GetFirstSubFeature(); int subcount = 0;
            while (s != null && subcount++ < 500)
            {
                if (String.Equals(s.Name, name, StringComparison.Ordinal)) return s;
                s = (SW.IFeature)s.GetNextSubFeature();
            }
            f = (SW.IFeature)f.GetNextFeature();
        }
        return null;
    }

    static object ReleaseOverConstrainingLocks(SW.IModelDoc2 doc)
    {
        var rows = new List<object>();
        foreach (string name in OverConstrainingLockMates)
        {
            var feature = FindMateFeature(doc, name);
            if (feature == null) throw new InvalidOperationException("Over-constraining lock mate is missing: " + name);
            if (feature.IsSuppressed()) { rows.Add(new { name = name, was_suppressed = true }); continue; }
            bool ok = feature.SetSuppression2(0, 1, null);
            if (!ok) ok = feature.SetSuppression(0);
            if (!ok || !feature.IsSuppressed())
                throw new InvalidOperationException("Could not release the over-constraining lock mate: " + name);
            rows.Add(new { name = name, was_suppressed = false, released = true });
        }
        return new
        {
            policy = "The V6 lock mates 锁定1/2/3 over-define the assembly; they are suppressed so the coincident and concentric mates can place the gasket and the pressure plate after a dimension change.",
            mates = rows.ToArray()
        };
    }

    // The V6 template carries a Flow Simulation project whose boundary conditions and
    // goals reference the four sealing caps.  Once the caps are deleted those
    // references dangle and SolidWorks raises a modal "some bodies are missing"
    // dialog, which blocks every unattended run.  The capless output therefore drops
    // the inherited project; each sample builds a fresh one after its caps are added.
    static List<string> FlowProjectNames(dynamic configuration)
    {
        var names = new List<string>();
        object raw = configuration.GetProjectNames();
        if (raw is Array) { foreach (object value in (Array)raw) if (value != null) names.Add(Convert.ToString(value)); }
        else if (raw != null) names.Add(Convert.ToString(raw));
        return names;
    }

    static object RemoveInheritedFlowProjects(SW.ISldWorks app, SW.IModelDoc2 doc)
    {
        dynamic nca = null;
        try
        {
            int pid = app.GetProcessID();
            nca = Activator.CreateInstance(Type.GetTypeFromProgID("NIKCommonApi2.BaseApiObject", true));
            if (!(bool)nca.LoadProductAPI2("Flow Simulation", "2026"))
                throw new InvalidOperationException("Cannot load the Flow Simulation 2026 API.");
            dynamic interactive = nca.Attach2RunningObject2(pid);
            if (interactive == null)
                throw new InvalidOperationException("Cannot attach the Flow API to SOLIDWORKS PID " + pid);
            dynamic document = interactive.ActiveDocument;
            dynamic configuration = document.ActiveConfiguration;
            var before = FlowProjectNames(configuration);
            var removed = new List<string>();
            foreach (string name in before)
            {
                if (configuration.RemoveProject(name)) removed.Add(name);
            }
            var after = FlowProjectNames(configuration);
            if (after.Count != 0)
                throw new InvalidOperationException("Inherited Flow projects remain after cleanup: " + String.Join(", ", after.ToArray()));
            return new { projects_before = before.ToArray(), removed = removed.ToArray(), projects_after = after.ToArray() };
        }
        finally
        {
            if (nca != null) { try { nca.UnloadProductAPI(); } catch { } }
        }
    }

    static void Rebuild(SW.IModelDoc2 doc)
    {
        bool ok = doc.ForceRebuild3(false);
        var errors = FeatureErrors(doc);
        if (!ok || errors.Count != 0) throw new InvalidOperationException("Rebuild failed: " + doc.GetTitle() + "; feature_errors=" + Serializer().Serialize(errors));
    }
    static void Save(SW.IModelDoc2 doc)
    {
        // doc.Save() rather than Save3(1, ...).  Controlled comparison on this template:
        // with Save3(1) a global-variable edit did NOT survive the reopen, with Save() it
        // did.  Save() can raise the modal "rebuild before saving?" prompt, so every caller
        // rebuilds first; a document with nothing pending does not prompt.
        try { doc.Save(); }
        catch (Exception ex) { throw new InvalidOperationException("Save failed: " + doc.GetTitle() + "; " + ex.Message); }
    }
    // Silent save, for documents that carry no equations.
    //
    // A plain Save() of an assembly that holds modified VIRTUAL components raises the modal
    // "另存为 / 该装配体包含未保存的虚拟零部件" dialog, which blocks every later COM call and
    // stalls an unattended run.  This template's assembly carries three virtual parts
    // (焊缝 / 入口管道 / 出口管道), so that prompt fires on every assembly save.
    //
    // Save3(1, ...) is silent.  The adapter avoids it for PARTS because it does not commit
    // equation edits - but the assembly has no equations, so it is the right call here.
    // (Measured: the same call was already used to save this template's assembly when the
    // lock mates were suppressed, with no loss.)
    //
    // Note: swWhileOpeningAssembliesAutoDismissMessages (564) does NOT cover this - it only
    // dismisses messages raised while OPENING an assembly, not while saving one.
    static void SaveSilent(SW.IModelDoc2 doc)
    {
        int errors = 0, warnings = 0;
        bool ok;
        try { ok = doc.Save3(1, ref errors, ref warnings); }
        catch (Exception ex) { throw new InvalidOperationException("Silent save failed: " + doc.GetTitle() + "; " + ex.Message); }
        if (!ok || errors != 0)
            throw new InvalidOperationException("Silent save failed: " + doc.GetTitle() + "; ok=" + ok.ToString() + "; errors=" + errors.ToString(CultureInfo.InvariantCulture));
    }
    static SW.IDimension Dimension(SW.IModelDoc2 doc, string parameter)
    {
        var dim = (SW.IDimension)doc.Parameter(parameter);
        if (dim == null) throw new InvalidOperationException("Missing dimension " + parameter + " in " + doc.GetTitle());
        return dim;
    }
    static void Set(Context context, string token, string parameter, double value, string variable)
    {
        var doc = context.Part(token); var dim = Dimension(doc, parameter);
        double before = dim.SystemValue;
        int status = dim.SetSystemValue3(value, 1, null);
        double after = Dimension(doc, parameter).SystemValue;
        context.Writes.Add(new WriteRecord { file = context.FileName(token), parameter = parameter, variable = variable, before_SI = before, requested_SI = value, after_SI = after, set_status = status });
        if (status != 0 || Math.Abs(after - value) > DimensionToleranceSI) throw new InvalidOperationException("Dimension assignment did not persist: " + token + "/" + parameter + "; status=" + status);
    }
    // The V7 template drives all 33 part dimensions from seven global variables through
    // equations, so the adapter sets the globals and lets CAD propagate them.  Writing the
    // dimensions directly fights the equations (observed: the values were silently
    // discarded on the next rebuild).  Globals hold millimetres for lengths and
    // arc-seconds for angles; see the note on phi_arcsec in the V7 build script.
    static readonly string[] CorePartTokens = { "03阀体", "04阀轴", "08大垫片", "09密封圈", "10压板", "11蝶板" };

    static int GlobalIndex(SW.IModelDoc2 doc, string name)
    {
        var em = (SW.IEquationMgr)doc.GetEquationMgr();
        string want = "\"" + name + "\"";
        for (int i = 0, n = em.GetCount(); i < n; i++)
        {
            string equation = em.get_Equation(i);
            if (equation.TrimStart().StartsWith(want, StringComparison.Ordinal)) return i;
        }
        return -1;
    }

    static string EquationText(SW.IModelDoc2 doc, string name)
    {
        int index = GlobalIndex(doc, name);
        if (index < 0) return "";
        return ((SW.IEquationMgr)doc.GetEquationMgr()).get_Equation(index).Replace(" ", "");
    }

    static double GlobalValueOf(SW.IModelDoc2 doc, string name)
    {
        // Read the value out of the equation text.  get_Value lags behind set_Equation
        // until SolidWorks next solves the document, so it cannot be used to confirm a
        // write; the geometry checks (LocalGeometry, caliper) then prove that the value
        // actually reached the model.
        int index = GlobalIndex(doc, name);
        if (index < 0) throw new InvalidOperationException("Global variable " + name + " is missing in " + doc.GetTitle());
        string equation = ((SW.IEquationMgr)doc.GetEquationMgr()).get_Equation(index);
        int equals = equation.IndexOf('=');
        if (equals < 0) throw new InvalidOperationException("Malformed equation: " + equation);
        return Convert.ToDouble(equation.Substring(equals + 1).Trim(), System.Globalization.CultureInfo.InvariantCulture);
    }

    sealed class GlobalAssignment
    {
        public string Name, Variable;
        public double Value;
        public GlobalAssignment(string name, double value, string variable)
        { Name = name; Value = value; Variable = variable; }
    }

    // One part, one open/activate/rebuild/save cycle, every requested global written inside it.
    //
    // The earlier form wrote ONE global per pass, so the six basic globals cost 6 x 6 = 36
    // full cycles.  Batching turns that into 6.  Measured before the change: a complete run
    // took 6.6 minutes, while the V6 adapter (which wrote dimensions directly with the parts
    // already open) took 1.0-1.6 minutes - the difference is almost entirely these cycles.
    //
    // Two properties are load-bearing and must not be relaxed:
    //   * equation writes only take effect on the ACTIVE document (ActivateDoc3),
    //   * the rebuild has to happen before the save, so the edit is committed and the modal
    //     "rebuild before saving?" prompt does not appear.
    static void SetGlobalsBatch(Context c, List<GlobalAssignment> assignments)
    {
        foreach (string token in CorePartTokens)
        {
            string key = c.Parts.Keys.Single(k => k.Contains(token));
            string partPath = Path.Combine(c.Folder, key);
            try { c.App.CloseDoc(c.Parts[key].GetTitle()); } catch { }
            SW.IModelDoc2 doc;
            // Hide only for the open itself, then restore.  Leaving the document hidden
            // while the equation is written stops the write from taking effect.
            try { c.App.DocumentVisible(false, 1); doc = Open(c.App, partPath, 1, false, ""); }
            finally { c.App.DocumentVisible(true, 1); }
            c.Parts[key] = doc;
            var em = (SW.IEquationMgr)doc.GetEquationMgr();
            // Only the active document accepts an equation write; a background part takes
            // set_Equation without error and silently keeps the old text.
            int activateErrors = 0;
            try { c.App.ActivateDoc3(doc.GetTitle(), false, 0, ref activateErrors); } catch { }
            foreach (var a in assignments)
            {
                int index = GlobalIndex(doc, a.Name);
                if (index < 0) throw new InvalidOperationException("Global variable " + a.Name + " is missing in " + token);
                double before = em.get_Value(index);
                string text = Convert.ToString(a.Value, System.Globalization.CultureInfo.InvariantCulture);
                string want = "\"" + a.Name + "\" = " + text;
                em.set_Equation(index, want);
                string now = EquationText(doc, a.Name);
                if (now != want.Replace(" ", ""))
                    throw new InvalidOperationException("Global variable assignment did not persist: " + token + "/" + a.Name + "; want=" + want + " got=" + now);
                c.Writes.Add(new WriteRecord { file = key, parameter = a.Name, variable = a.Variable, before_SI = before, requested_SI = a.Value, after_SI = a.Value, set_status = 0 });
            }
            doc.ForceRebuild3(false);
            Save(doc);
        }
    }

    static void SetGlobal(Context c, string name, double value, string variable)
    {
        SetGlobalsBatch(c, new List<GlobalAssignment> { new GlobalAssignment(name, value, variable) });
    }

    // V7 templates drive every dimension from seven global variables through equations; the
    // dimensions themselves carry no independent value.  V6 templates carry the dimensions as
    // plain dimensions and have no globals at all.  The two need completely different write
    // paths, and the V6 one is about four times cheaper per write because it never reopens a
    // document or re-solves equations: measured 1.3-1.6 min for a full V6 run against
    // 6.6 min for V7 on the same design.  Detect it from the template itself rather than
    // threading a flag through the PowerShell entry point.
    static bool UsesGlobalVariables(Context c)
    {
        try { return GlobalIndex(c.Part("11蝶板"), "c") >= 0; }
        catch { return false; }
    }

    static void SetBasic(Context c, Design d)
    {
        if (!UsesGlobalVariables(c)) { SetBasicPlainDimensions(c, d); return; }
        SetGlobalsBatch(c, new List<GlobalAssignment>
        {
            new GlobalAssignment("c", d.c_mm, "c_mm"),
            new GlobalAssignment("e", d.e_mm, "e_mm"),
            new GlobalAssignment("phi_arcsec", d.phi_deg * 3600.0, "phi_deg"),
            new GlobalAssignment("alpha_arcsec", d.alpha_deg * 3600.0, "alpha_deg"),
            new GlobalAssignment("bm", d.bm_mm, "bm_mm"),
            new GlobalAssignment("ds", d.ds_mm, "ds_mm"),
        });
    }

    // The V6 write path: 22 independent dimensions, straight onto parts that Context already
    // opened.  Values are SI, so lengths are divided by 1000 and angles converted to radians.
    // The dimension list and the alpha/phi conventions match 七变量CAD映射说明.md section 1:
    // phi drives five angles, alpha is stored whole in the gasket and halved everywhere else.
    static void SetBasicPlainDimensions(Context c, Design d)
    {
        Set(c, "03阀体", "D2@草图3", d.c_mm / 1000, "c_mm");
        Set(c, "08大垫片", "D10@草图1", d.c_mm / 1000, "c_mm");
        Set(c, "09密封圈", "D3@草图1", d.c_mm / 1000, "c_mm");
        Set(c, "10压板", "D2@草图1", d.c_mm / 1000, "c_mm");
        Set(c, "11蝶板", "D2@草图1", d.c_mm / 1000, "c_mm");
        // See DiscFarFaceToStemOffsetMm: D2 alone shears the disc (cone slides, plate stays) and
        // the ring interference comes back.  This second write moves the plate with the cone.
        Set(c, "11蝶板", "D6@草图1", (d.c_mm + DiscFarFaceToStemOffsetMm) / 1000, "c_mm+offset");

        Set(c, "03阀体", "D30@草图4", d.e_mm / 1000, "e_mm");
        Set(c, "03阀体", "D4@草图6", d.e_mm / 1000, "e_mm");
        Set(c, "09密封圈", "D9@草图1", d.e_mm / 1000, "e_mm");
        Set(c, "11蝶板", "D1@草图1", d.e_mm / 1000, "e_mm");
        Set(c, "11蝶板", "D1@基准面2", d.e_mm / 1000, "e_mm");

        Set(c, "03阀体", "D3@草图5", Radians(d.phi_deg), "phi_deg");
        Set(c, "08大垫片", "D5@草图1", Radians(d.phi_deg), "phi_deg");
        Set(c, "09密封圈", "D5@草图1", Radians(d.phi_deg), "phi_deg");
        Set(c, "10压板", "D4@草图1", Radians(d.phi_deg), "phi_deg");
        Set(c, "11蝶板", "D2@草图3", Radians(d.phi_deg), "phi_deg");

        Set(c, "03阀体", "D2@草图5", Radians(d.alpha_deg / 2), "alpha_deg/2");
        Set(c, "08大垫片", "D7@草图1", Radians(d.alpha_deg), "alpha_deg");
        Set(c, "09密封圈", "D6@草图1", Radians(d.alpha_deg / 2), "alpha_deg/2");
        Set(c, "10压板", "D7@草图1", Radians(d.alpha_deg / 2), "alpha_deg/2");
        Set(c, "11蝶板", "D4@草图3", Radians(d.alpha_deg / 2), "alpha_deg/2");

        Set(c, "09密封圈", "D2@草图1", d.bm_mm / 1000, "bm_mm");
        Set(c, "03阀体", "D25@草图4", d.ds_mm / 1000, "ds_mm");
        Set(c, "04阀轴", "D4@草图1", d.ds_mm / 1000, "ds_mm");
        Set(c, "09密封圈", "D8@草图1", d.ds_mm / 1000, "ds_mm");
        Set(c, "11蝶板", "D2@草图2", d.ds_mm / 1000, "ds_mm");
    }
    static void SetConstruction(Context c, double sMm)
    {
        if (sMm <= 2 * RadialDifferenceMm || Double.IsNaN(sMm) || Double.IsInfinity(sMm)) throw new InvalidOperationException("Invalid internal cone-circle diameter.");
        // Geometric range guard, measured on the V6 master (2026-09-20).
        //
        // `s` positions the seal cone through the 194.37 construction line, and the cone's radius
        // tracks s/2 almost exactly (measured: dRadius/ds = 0.4948 over the audited design, and
        // the master's cone sits at 97.185 = 194.37/2).  09密封圈's material spans radius 70.0 to
        // 108.1723, so the cone leaves the material - and the part silently becomes wrong - outside
        // s = 140 .. 216.34.  Measured at s = 138 the solid collapses (volume 89696 -> 1960131,
        // cone half-angle 17.75 -> 9.50); at s = 150 the cut almost consumes the ring
        // (89696 -> 6821).
        //
        // Upper bound is set by 03阀体, swept 2026-09-20: s = 191.49 / 196.59 / 200.44 all rebuild
        // clean, s ~= 201.4 fails with feature_errors=[{"name":"分割线1"}] and the run aborts.  The
        // cliff is sharp, so 200.0 is used rather than running to the measured edge.  This is NOT
        // 09密封圈's limit - that one moved out when the ring's outer edge started following s
        // (see "derived_ring_outer_edge"); the body is now the binding part.
        //
        // Lower bound is unchanged - it comes from the ring's Ø140 bore, a real surface that must
        // not move.
        //
        // These bounds are derived from 09密封圈 only - the other five parts have their own limits
        // that have not been swept yet, so this guard is necessary but may not be sufficient.
        const double MinInternalConeDiameterMm = 145.0;
        const double MaxInternalConeDiameterMm = 200.0;   // MEASURED, not derived: 03阀体 分割线1 fails to rebuild above s~200.5
        if (sMm < MinInternalConeDiameterMm || sMm > MaxInternalConeDiameterMm)
            throw new InvalidOperationException(
                "internal cone diameter " + sMm.ToString("F2") + " mm is outside the measured geometric range "
                + MinInternalConeDiameterMm.ToString("F1") + ".." + MaxInternalConeDiameterMm.ToString("F1")
                + " mm; the seal cone would leave 09密封圈's material (radius 70.0..108.1723).");
        if (!UsesGlobalVariables(c))
        {
            Set(c, "03阀体", "D5@草图5", sMm / 1000, "internal_s_mm");
            Set(c, "08大垫片", "D6@草图1", sMm / 1000, "internal_s_mm");
            Set(c, "09密封圈", "D7@草图1", sMm / 1000, "internal_s_mm");
            Set(c, "10压板", "D5@草图1", sMm / 1000, "internal_s_mm");
            Set(c, "11蝶板", "D3@草图3", sMm / 1000, "internal_s_mm");
            // The cone dimensions derived from s/2-1.575.  In V7 these are equations of "s" and
            // the adapter never computes them; in V6 they are independent dimensions and have to
            // be written by hand or the cone circle and the cone surface drift apart.  10压板 is
            // no longer among them - see the note on D8@草图1 below.
            double radial = sMm / 2 - RadialDifferenceMm;
            Set(c, "11蝶板", "D5@草图3", radial / 1000, "derived_disc_cone_radius");
            // 10压板 D8@草图1 is deliberately NOT written: as of 2026-09-20 that dimension is the
            // constant 1.575 between the seal-ring anchor point and the end of the 194.37
            // construction line.  It USED to be 95.61 measured to a fixed point, which pinned the
            // anchor and stopped the plate's cone following s; the two were swapped so the 1.575
            // now drives and the 95.61 is driven.  Writing s/2-1.575 here would overwrite the
            // constant with ~95.6 and break the sketch.  The plate now inherits s through the
            // construction line exactly like 09密封圈.
            Set(c, "08大垫片", "D8@草图1", 2 * radial / 1000, "derived_gasket_cone_diameter");
            Set(c, "09密封圈", "D10@草图1", (sMm / 2 + RingOuterMarginMm - RingBoreRadiusMm) / 1000, "derived_ring_outer_edge");
            Set(c, "10压板", "D13@草图1", (sMm / 2 + PlateOuterMarginMm - PlateStockInnerEdgeMm) / 1000, "derived_plate_outer_edge");
            // V6 writes dimensions rather than equations, and SetSystemValue3 only marks the
            // document dirty.  Rebuild here, because Evaluate() below no longer rebuilds -
            // in V7 that rebuild happened inside the write batch.
            foreach (var part in c.Parts.Values) Rebuild(part);
            return;
        }
        // The three derived cone dimensions are equations of "s" in V7 (s/2-1.575 and
        // s-3.15), so setting the global is enough and the adapter no longer computes them.
        SetGlobal(c, "s", sMm, "internal_s_mm");
    }

    static SW.IBody2[] Bodies(SW.IModelDoc2 doc)
    {
        var bodies = A(((SW.IPartDoc)doc).GetBodies2(0, false)).Cast<SW.IBody2>().ToArray();
        if (bodies.Length == 0) throw new InvalidOperationException("No solid body in " + doc.GetTitle());
        return bodies;
    }
    static double Support(SW.IBody2[] bodies, double y, double z, out double[] point)
    {
        double best = Double.NegativeInfinity; point = null;
        foreach (var body in bodies)
        {
            double px, py, pz;
            if (!body.GetExtremePoint(0, y, z, out px, out py, out pz)) throw new InvalidOperationException("GetExtremePoint failed.");
            if (new[] { px, py, pz }.Any(v => Double.IsNaN(v) || Double.IsInfinity(v))) throw new InvalidOperationException("Nonfinite support point.");
            double value = y * py + z * pz;
            if (value > best) { best = value; point = new[] { px, py, pz }; }
        }
        return best;
    }
    sealed class WidthValue
    {
        public double Angle, Width;
        public double[] Plus, Minus;
    }
    sealed class AngularInterval
    {
        public double Left, Right, LeftWidth, RightWidth;
        public double Upper { get { return Math.Max(LeftWidth, RightWidth) / Math.Cos((Right - Left) / 2); } }
    }

    /// <summary>
    /// For the interval containing a true diameter-vector direction, one endpoint
    /// is at most half an interval away. Its width is at least D*cos(half gap).
    /// Therefore max(interval endpoint bounds) is a global bound on D.
    /// This proof needs neither differentiability nor unimodality of width.
    /// </summary>
    static CaliperResult AdaptiveCaliper(Func<double, WidthValue> query, int callsPerDirection)
    {
        const int initialCount = 16, maxDirections = 8192;
        var samples = new List<WidthValue>();
        var intervals = new List<AngularInterval>();
        WidthValue best = null;
        Func<double, WidthValue> measured = angle =>
        {
            var v = query(angle);
            if (v == null || Double.IsNaN(v.Width) || Double.IsInfinity(v.Width) || v.Width < 0)
                throw new InvalidOperationException("Invalid projected caliper width.");
            v.Angle = angle; samples.Add(v);
            if (best == null || v.Width > best.Width) best = v;
            return v;
        };
        for (int i = 0; i < initialCount; i++) measured(Math.PI * i / initialCount);
        for (int i = 0; i < initialCount; i++)
            intervals.Add(new AngularInterval { Left = Math.PI * i / initialCount, Right = Math.PI * (i + 1) / initialCount,
                LeftWidth = samples[i].Width, RightWidth = samples[(i + 1) % initialCount].Width });
        double upper;
        while (true)
        {
            int worst = 0;
            for (int i = 1; i < intervals.Count; i++) if (intervals[i].Upper > intervals[worst].Upper) worst = i;
            // Tiny arithmetic padding covers ordinary floating point roundoff;
            // it does not represent or hide the CAD kernel's support precision.
            double roundoff = 1e-12 * Math.Max(1, best.Width);
            upper = Math.Max(best.Width, intervals[worst].Upper) + roundoff;
            if (upper - best.Width <= CaliperBandMm) break;
            if (samples.Count >= maxDirections) throw new InvalidOperationException("Adaptive caliper exhausted its direction budget before reaching the bound tolerance.");
            var old = intervals[worst];
            double middle = (old.Left + old.Right) / 2;
            if (middle == old.Left || middle == old.Right) throw new InvalidOperationException("Adaptive caliper angular subdivision stalled.");
            var v = measured(middle);
            intervals[worst] = new AngularInterval { Left = old.Left, Right = middle, LeftWidth = old.LeftWidth, RightWidth = v.Width };
            intervals.Add(new AngularInterval { Left = middle, Right = old.Right, LeftWidth = v.Width, RightWidth = old.RightWidth });
        }
        var result = new CaliperResult();
        result.lower_mm = best.Width; result.upper_mm = upper; result.midpoint_mm = (best.Width + upper) / 2;
        result.band_mm = upper - best.Width;
        result.directions = samples.Count; result.support_calls = samples.Count * callsPerDirection;
        result.step_deg = Degrees(intervals.Max(x => x.Right - x.Left));
        result.peak_angle_deg = Degrees(best.Angle); result.peak_plus_point_m = best.Plus; result.peak_minus_point_m = best.Minus;
        result.bound = "Adaptive periodic angular intervals: L=max sampled width; U=max_i(max(w(a_i),w(b_i))/cos((b_i-a_i)/2)) plus arithmetic padding. step_deg is the largest remaining interval, not a uniform spacing. CAD kernel support precision is additional.";
        result.samples = samples.OrderBy(x => x.Angle).Select(x => new CaliperSample { angle_deg = Degrees(x.Angle), width_mm = x.Width }).ToList();
        return result;
    }

    public static CaliperResult MeasureCaliper(SW.IModelDoc2 disc)
    {
        // Obtain new bodies after every caller rebuild/configuration transition.
        var bodies = Bodies(disc);
        CaliperResult result = AdaptiveCaliper(angle =>
        {
            double y = Math.Cos(angle), z = Math.Sin(angle); double[] plus, minus;
            double width = (Support(bodies, y, z, out plus) + Support(bodies, -y, -z, out minus)) * 1000;
            return new WidthValue { Width = width, Plus = plus, Minus = minus };
        }, 2 * bodies.Length);
        if (result.lower_mm <= 0) throw new InvalidOperationException("Disc projection has no positive diameter.");
        return result;
    }

    /// <summary>Pure mathematical verification: never creates or accesses COM.</summary>
    public static object CaliperSelfTest()
    {
        var tests = new List<object>();
        Action<string, double, CaliperResult> check = (name, truth, r) =>
        {
            if (r.lower_mm > truth + 1e-8 || r.upper_mm < truth - 1e-8 || r.band_mm > CaliperBandMm + 1e-12)
                throw new InvalidOperationException("Caliper bound self-test failed: " + name);
            tests.Add(new { name = name, true_diameter_mm = truth, lower_mm = r.lower_mm, upper_mm = r.upper_mm,
                band_mm = r.band_mm, queried_directions = r.directions, max_remaining_step_deg = r.step_deg,
                previous_uniform_directions_for_200mm = 900, completed = true });
        };
        Action<string, double, double, double> ellipse = (name, major, minor, rotationDeg) =>
        {
            double rotation = Radians(rotationDeg);
            var r = AdaptiveCaliper(angle =>
            {
                double ca = Math.Cos(angle - rotation), sa = Math.Sin(angle - rotation);
                return new WidthValue { Width = 2 * Math.Sqrt(major * major * ca * ca + minor * minor * sa * sa) };
            }, 0);
            check(name, 2 * Math.Max(major, minor), r);
        };
        ellipse("axis_aligned_ellipse", 100, 55, 0);
        ellipse("rotated_ellipse_17p37deg", 100, 55, 17.37);
        ellipse("rotated_ellipse_across_period_boundary", 100, 55, 179.91);
        ellipse("disc_like_near_circle", 95.6587069476, 94.7436385153, 0);
        ellipse("rotated_near_circle", 100, 99.5, 8.25);
        ellipse("circle", 100, 100, 0);
        ellipse("large_rotated_ellipse", 500, 25, 83.125);
        var pointSets = new List<double[][]>();
        pointSets.Add(new[] { new[] { -100.0, 0 }, new[] { 100.0, 0 }, new[] { 0.0, 70 }, new[] { 3.0, -51 } });
        pointSets.Add(new[] { new[] { 35.0, 71 }, new[] { 123.0, -8 }, new[] { -45.0, 12 }, new[] { -18.0, -107 }, new[] { 36.0, -59 } });
        pointSets.Add(new[] { new[] { 7.0, -1 }, new[] { 7.0, 199 } }); // Line projection includes exact zero widths.
        var random = new Random(271828);
        for (int k = 0; k < 20; k++)
        {
            var points = new List<double[]>();
            for (int i = 0; i < 19 + k; i++) points.Add(new[] { random.NextDouble() * 260 - 35, random.NextDouble() * 170 - 100 });
            pointSets.Add(points.ToArray());
        }
        for (int k = 0; k < pointSets.Count; k++)
        {
            double[][] points = pointSets[k]; double truth = 0;
            for (int i = 0; i < points.Length; i++)
                for (int j = i + 1; j < points.Length; j++)
                    truth = Math.Max(truth, Math.Sqrt(Math.Pow(points[i][0] - points[j][0], 2) + Math.Pow(points[i][1] - points[j][1], 2)));
            var r = AdaptiveCaliper(angle =>
            {
                double y = Math.Cos(angle), z = Math.Sin(angle);
                double hi = Double.NegativeInfinity, lo = Double.PositiveInfinity;
                foreach (var p in points) { double v = y * p[0] + z * p[1]; hi = Math.Max(hi, v); lo = Math.Min(lo, v); }
                return new WidthValue { Width = hi - lo };
            }, 0);
            check("finite_point_hull_" + k, truth, r);
        }
        return new { status = "all_math_tests_passed", cad_executed = false, count = tests.Count, tests = tests };
    }

    static bool DiameterMatches(CaliperResult c, double target)
    {
        return c.band_mm <= CaliperBandMm && c.lower_mm >= target - DiameterToleranceMm && c.upper_mm <= target + DiameterToleranceMm;
    }
    static CaliperResult Evaluate(Context c, double s, double target)
    {
        var row = O(); row["internal_s_mm"] = s; row["started_utc"] = DateTime.UtcNow.ToString("o"); c.SolveTrace.Add(row);
        Stage(c, "diameter_evaluate_start", new { internal_s_mm = s, target_mm = target });
        try
        {
            SetConstruction(c, s);
            // SetConstruction already force-rebuilt every part inside its write batch, so a
            // second Rebuild() here repeated the work for nothing - about 10 s per iteration,
            // and the s search runs this many times.  Keep the error check, drop the rebuild.
            foreach (var p in c.Parts.Values)
            {
                Stage(c, "rebuild_part_start", p.GetTitle());
                var rebuildErrors = FeatureErrors(p);
                if (rebuildErrors.Count != 0)
                    throw new InvalidOperationException("Rebuild failed: " + p.GetTitle() + "; feature_errors=" + Serializer().Serialize(rebuildErrors));
                Stage(c, "rebuild_part_complete", p.GetTitle());
            }
            Stage(c, "caliper_evaluate_start", new { internal_s_mm = s });
            CaliperResult m = MeasureCaliper(c.Part("11蝶板"));
            row["lower_mm"] = m.lower_mm; row["upper_mm"] = m.upper_mm; row["residual_mid_mm"] = m.midpoint_mm - target;
            row["completed"] = true;
            Stage(c, "diameter_evaluate_complete", new { internal_s_mm = s, lower_mm = m.lower_mm, upper_mm = m.upper_mm, directions = m.directions });
            return m;
        }
        catch (Exception ex) { row["completed"] = false; row["error"] = ex.Message; Stage(c, "diameter_evaluate_failed", ex.Message); throw; }
    }
    static double SolveConstruction(Context c, Design d, out CaliperResult final)
    {
        double s0 = Dimension(c.Part("11蝶板"), "D3@草图3").SystemValue * 1000;
        CaliperResult m0 = Evaluate(c, s0, d.Dmax_mm);
        if (DiameterMatches(m0, d.Dmax_mm)) { final = m0; return s0; }
        double f0 = m0.midpoint_mm - d.Dmax_mm;
        double a = s0, fa = f0, b = s0, fb = f0, step = Math.Max(0.5, Math.Abs(f0) / 0.7 * 1.2);
        bool bracketed = false;
        for (int i = 0; i < 10; i++)
        {
            double s = s0 + (f0 > 0 ? -step : step);
            if (s <= 2 * RadialDifferenceMm + 0.01 || s > 5 * d.Dmax_mm) throw new InvalidOperationException("No valid internal diameter bracket within the guarded template range.");
            var m = Evaluate(c, s, d.Dmax_mm);
            if (DiameterMatches(m, d.Dmax_mm)) { final = m; return s; }
            double f = m.midpoint_mm - d.Dmax_mm;
            if (f * f0 < 0)
            {
                if (s < s0) { a = s; fa = f; b = s0; fb = f0; }
                else { a = s0; fa = f0; b = s; fb = f; }
                bracketed = true; break;
            }
            step *= 1.8;
        }
        if (!bracketed) throw new InvalidOperationException("Dmax target could not be bracketed; no geometry success is asserted.");
        for (int i = 0; i < 28; i++)
        {
            double s = b - fb * (b - a) / (fb - fa);
            double low = a + 0.1 * (b - a), high = b - 0.1 * (b - a);
            if (Double.IsNaN(s) || s <= low || s >= high) s = (a + b) / 2;
            var m = Evaluate(c, s, d.Dmax_mm);
            if (DiameterMatches(m, d.Dmax_mm)) { final = m; return s; }
            double f = m.midpoint_mm - d.Dmax_mm;
            if (f * fa <= 0) { b = s; fb = f; } else { a = s; fa = f; }
        }
        throw new InvalidOperationException("Dmax solve exhausted its iteration limit.");
    }

    static List<Tuple<SW.IFace2, SW.ISurface>> Faces(SW.IModelDoc2 doc)
    {
        var list = new List<Tuple<SW.IFace2, SW.ISurface>>();
        foreach (var b in Bodies(doc))
            foreach (object o in A(b.GetFaces())) { var f = (SW.IFace2)o; list.Add(Tuple.Create(f, (SW.ISurface)f.GetSurface())); }
        return list;
    }
    static double[] MainShaft(SW.IModelDoc2 doc)
    {
        return Faces(doc).Where(x => x.Item2.IsCylinder())
            .Where(x => { var p = D(x.Item2.CylinderParams); return Math.Abs(p[3]) > .999999 && Math.Abs(p[1]) < 1e-6 && Math.Abs(p[2]) < 1e-6; })
            .OrderByDescending(x => x.Item1.GetArea()).Select(x => D(x.Item2.CylinderParams)).First();
    }
    static double[] BodyShaft(SW.IModelDoc2 doc)
    {
        return Faces(doc).Where(x => x.Item2.IsCylinder())
            .Where(x => { var p = D(x.Item2.CylinderParams); return Math.Abs(p[5]) > .999999 && Math.Abs(p[0]) < 1e-6 && p[6] >= .02; })
            .OrderByDescending(x => x.Item1.GetArea()).Select(x => D(x.Item2.CylinderParams)).First();
    }
    static double[] FlowAxis(SW.IModelDoc2 doc)
    {
        return Faces(doc).Where(x => x.Item2.IsCylinder())
            .Where(x => { var p = D(x.Item2.CylinderParams); return Math.Abs(p[3]) > .999999 && Math.Abs(p[1]) < 1e-6 && Math.Abs(p[2]) < 1e-6 && Math.Abs(p[6] - .105) < 1e-6; })
            .OrderByDescending(x => x.Item1.GetArea()).Select(x => D(x.Item2.CylinderParams)).First();
    }
    static double[] SealCone(SW.IModelDoc2 doc)
    {
        var cones = Faces(doc).Where(x => x.Item2.IsCone()).ToArray();
        if (cones.Length != 1) throw new InvalidOperationException("Expected one semantic seal cone.");
        return D(cones[0].Item2.ConeParams2);
    }
    static double[] SealPlanes(SW.IModelDoc2 doc)
    {
        var xs = Faces(doc).Where(x => x.Item2.IsPlane()).Select(x => D(x.Item2.PlaneParams))
            .Where(x => Math.Abs(x[0]) > .999999).Select(x => x[3]).OrderBy(x => x).ToArray();
        if (xs.Length != 2) throw new InvalidOperationException("Expected the two seal-sheet end planes.");
        return xs;
    }
    static double[] World(SW.IComponent2 c, double[] p, bool vector)
    {
        var t = D(((SW.IMathTransform)c.Transform2).ArrayData);
        if (t.Length < 13 || Math.Abs(t[12] - 1) > 1e-9) throw new InvalidOperationException("Unsupported component transform scale.");
        return new[] { p[0] * t[0] + p[1] * t[3] + p[2] * t[6] + (vector ? 0 : t[9]), p[0] * t[1] + p[1] * t[4] + p[2] * t[7] + (vector ? 0 : t[10]), p[0] * t[2] + p[1] * t[5] + p[2] * t[8] + (vector ? 0 : t[11]) };
    }
    static double LineDistance(double[] p, double[] u, double[] q, double[] v)
    {
        u = Unit(u); v = Unit(v); var cross = Cross(u, v); var delta = Sub(q, p);
        if (Norm(cross) > 1e-10) return Math.Abs(Dot(delta, cross)) / Norm(cross) * 1000;
        return Norm(Cross(delta, u)) * 1000;
    }
    static void Near(string name, double actual, double expected, double tolerance)
    {
        if (Double.IsNaN(actual) || Math.Abs(actual - expected) > tolerance)
            throw new InvalidOperationException(name + " measurement mismatch: actual=" + actual.ToString("R", CultureInfo.InvariantCulture) + ", expected=" + expected.ToString("R", CultureInfo.InvariantCulture));
    }
    static SW.IComponent2[] Components(SW.IModelDoc2 assembly)
    {
        return A(((SW.IAssemblyDoc)assembly).GetComponents(false)).Cast<SW.IComponent2>().ToArray();
    }
    static object References(SW.IModelDoc2 assembly, string folder)
    {
        var refs = new List<object>();
        foreach (var c in Components(assembly))
        {
            string path = c.GetPathName(); bool isVirtual = c.IsVirtual;
            if (!isVirtual) Under(path, folder, true);
            if (c.GetModelDoc2() == null) throw new InvalidOperationException("Unresolved component: " + c.Name2);
            refs.Add(new { name = c.Name2, path = path, is_virtual = isVirtual });
        }
        return refs;
    }
    static void RebuildV6Assembly(SW.IModelDoc2 doc)
    {
        bool ok = doc.ForceRebuild3(false);
        var errors = FeatureErrors(doc);
        if (!ok || errors.Count != 0)
            throw new InvalidOperationException("V6 assembly rebuild must be clean; feature errors: " + Serializer().Serialize(errors));
    }
    static object FreezeV6AssemblyAtSaved45(Context c)
    {
        var assembly = (SW.IAssemblyDoc)c.Assembly;
        var components = Components(c.Assembly);
        var saved = components.ToDictionary(x => x.Name2,
            x => D(((SW.IMathTransform)x.Transform2).ArrayData), StringComparer.Ordinal);
        var mateGroup = FindFeature(c.Assembly, "配合");
        if (mateGroup == null) throw new InvalidOperationException("V6 mate group was not found.");
        c.Assembly.ClearSelection2(true);
        if (!mateGroup.Select2(false, 0)) throw new InvalidOperationException("Could not select the V6 mate group.");
        bool mateGroupDeleted = c.Assembly.Extension.DeleteSelection2(0);
        if (!mateGroupDeleted || FindFeature(c.Assembly, "配合") != null)
            throw new InvalidOperationException("Could not remove the topology-sensitive V6 mate group.");
        var math = (SW.IMathUtility)c.App.GetMathUtility();
        var fixedNames = new List<string>();
        foreach (var component in components)
        {
            component.Transform2 = (SW.MathTransform)math.CreateTransform(saved[component.Name2]);
            c.Assembly.ClearSelection2(true);
            if (!component.Select4(false, null, false))
                throw new InvalidOperationException("Could not select component for fixed-position conversion: " + component.Name2);
            assembly.FixComponent();
            fixedNames.Add(component.Name2);
        }
        c.Assembly.ClearSelection2(true);
        RebuildV6Assembly(c.Assembly);
        return new { policy = "saved 45-degree transforms + removed topology-sensitive mate group + fixed components",
            mate_group_deleted = mateGroupDeleted, fixed_components = fixedNames.ToArray(), feature_errors = FeatureErrors(c.Assembly) };
    }
    static void ValidateFixedEndcaps(SW.IModelDoc2 assembly)
    {
        // A cap-less template is the normal case as of 2026-09-19: zero caps is fine, four caps
        // (an older template) is fine, anything in between means a component was lost.
        var components = Components(assembly);
        int found = 0;
        for (int i = 1; i <= 4; i++)
        {
            string token = "封盖" + i.ToString(CultureInfo.InvariantCulture);
            var matches = components.Where(x => x.Name2.Contains(token)).ToArray();
            if (matches.Length == 0) continue;
            if (matches.Length != 1) throw new InvalidOperationException("Expected one resolved fixed endcap component: " + token);
            if (matches[0].GetModelDoc2() == null) throw new InvalidOperationException("Fixed endcap is suppressed or unresolved: " + token);
            found++;
        }
        if (found != 0 && found != 4)
            throw new InvalidOperationException("Expected zero or four resolved endcaps; found " + found.ToString(CultureInfo.InvariantCulture));
    }
    static void BindCorePartsFromAssembly(Context c)
    {
        c.Parts.Clear();
        string[] tokens = { "03阀体", "04阀轴", "08大垫片", "09密封圈", "10压板", "11蝶板" };
        foreach (var component in Components(c.Assembly))
        {
            string path = component.GetPathName();
            if (String.IsNullOrEmpty(path) || !tokens.Any(t => Path.GetFileName(path).Contains(t))) continue;
            var doc = (SW.IModelDoc2)component.GetModelDoc2();
            if (doc == null) throw new InvalidOperationException("Core component is unresolved: " + component.Name2);
            c.Parts[Path.GetFileName(path)] = doc;
        }
        if (c.Parts.Count != 6) throw new InvalidOperationException("Expected six resolved core components after assembly-only reload.");
    }
    static object CreateCaplessOutput(Context c)
    {
        string assemblyPath = Directory.GetFiles(c.Folder, "*.SLDASM").Single();
        CloseFolder(c.App, c.Folder); c.Assembly = null; c.Parts.Clear();
        var doc = Open(c.App, assemblyPath, 2, false, AssemblyConfiguration);
        var assembly = (SW.IAssemblyDoc)doc;
        var lockRelease = ReleaseOverConstrainingLocks(doc);
        Rebuild(doc);
        // The inherited Flow project is removed here, after the rebuild.  Measured behaviour:
        // running it before the rebuild (while the document is untouched) makes
        // RemoveProject die with RPC_E_DISCONNECTED (0x80010108) - but only sometimes, which
        // is worse than always.  The reliable fix is to keep no Flow project in the template
        // at all: the adapter discards it on every run anyway, so a template without one has
        // nothing to remove, nothing to fail on, and cannot raise the modal
        // "面<1>@封盖1<1> 未在固体和流体区域之间的边界上" dialog either.
        var flowCleanup = RemoveInheritedFlowProjects(c.App, doc);
        // The current template has no caps, so this normally does nothing but save.  Older
        // templates still carry four, and they are deleted exactly as before - the output is
        // capless either way, which is what every later stage consumes.
        var caps = Components(doc).Where(x => x.Name2.StartsWith("封盖", StringComparison.Ordinal)).ToArray();
        var capNames = caps.Select(x => x.Name2).ToArray();
        var removedFiles = new List<string>();
        if (caps.Length == 4)
        {
            doc.ClearSelection2(true);
            bool append = false;
            foreach (var cap in caps)
            {
                if (!cap.Select4(append, null, false)) throw new InvalidOperationException("Could not select cap component: " + cap.Name2);
                append = true;
            }
            if (!assembly.DeleteSelections(0)) throw new InvalidOperationException("SolidWorks could not delete the four cap components.");
            if (Components(doc).Any(x => x.Name2.StartsWith("封盖", StringComparison.Ordinal)))
                throw new InvalidOperationException("A cap component remained in the capless assembly.");
            // The saved capless assembly is the artifact every later stage consumes, so it
            // is verified for real here.  Deleting a component triggers a rebuild, and
            // until now nothing checked the mate errors that rebuild can produce.
            Rebuild(doc);
            SaveSilent(doc);
            CloseFolder(c.App, c.Folder);
            for (int i = 1; i <= 4; i++)
            {
                string path = Path.Combine(c.Folder, "封盖" + i + ".SLDPRT");
                if (!File.Exists(path)) throw new FileNotFoundException("Cap file missing before capless cleanup.", path);
                File.Delete(path); removedFiles.Add(Path.GetFileName(path));
            }
        }
        else
        {
            if (caps.Length != 0) throw new InvalidOperationException("Expected zero or four caps; found " + caps.Length.ToString(CultureInfo.InvariantCulture));
            SaveSilent(doc);
            CloseFolder(c.App, c.Folder);
        }
        c.Assembly = Open(c.App, assemblyPath, 2, true, AssemblyConfiguration);
        var remaining = Components(c.Assembly);
        if (remaining.Any(x => x.Name2.StartsWith("封盖", StringComparison.Ordinal)))
            throw new InvalidOperationException("Cap component returned after capless assembly reopen.");
        int externalCore = remaining.Count(x => {
            string p = x.GetPathName();
            return !String.IsNullOrEmpty(p) && !x.IsVirtual && !Path.GetFileName(p).StartsWith("封盖", StringComparison.OrdinalIgnoreCase);
        });
        if (externalCore != 6) throw new InvalidOperationException("Capless assembly must retain exactly six external core components.");
        return new { cap_components_removed = capNames, cap_files_removed = removedFiles.ToArray(),
            remaining_component_count = remaining.Length, external_core_count = externalCore,
            lock_release = lockRelease, flow_project_cleanup = flowCleanup };
    }
    static object AssemblyGeometry(Context c, Design d, bool closed)
    {
        var comps = Components(c.Assembly);
        var body = comps.Single(x => x.Name2.Contains("03阀体"));
        var shaft = comps.Single(x => x.Name2.Contains("04阀轴"));
        var seal = comps.Single(x => x.Name2.Contains("09密封圈"));
        var disc = comps.Single(x => x.Name2.Contains("11蝶板"));
        var bp = BodyShaft(c.Part("03阀体")); var sp = MainShaft(c.Part("04阀轴")); var fp = FlowAxis(c.Part("03阀体"));
        var bodyP = World(body, bp.Take(3).ToArray(), false); var bodyU = Unit(World(body, bp.Skip(3).Take(3).ToArray(), true));
        var shaftP = World(shaft, sp.Take(3).ToArray(), false); var shaftU = Unit(World(shaft, sp.Skip(3).Take(3).ToArray(), true));
        double offset = LineDistance(bodyP, bodyU, shaftP, shaftU), parallel = Math.Abs(Dot(bodyU, shaftU));
        if (Double.IsNaN(offset) || Double.IsInfinity(offset) || offset > 5.0)
            throw new InvalidOperationException("V6 saved-transform shaft/body axis offset is invalid: " + offset.ToString("R", CultureInfo.InvariantCulture) + " mm");
        Near("shaft/body parallelism", parallel, 1, 1e-8);
        var report = O(); report["axis_offset_mm"] = offset; report["axis_abs_dot"] = parallel;
        report["axis_alignment_policy"] = "Preserve the user-validated v6 saved transforms; record finite offset <= 5 mm and require parallel axes. Flow feature rebuild is the sealing gate.";
        report["body_axis_point_m"] = bodyP; report["shaft_axis_point_m"] = shaftP;
        if (!closed) return report;

        var flowP = World(body, fp.Take(3).ToArray(), false); var flowU = Unit(World(body, fp.Skip(3).Take(3).ToArray(), true));
        var planes = SealPlanes(c.Part("09密封圈"));
        var planeP = World(seal, new[] { (planes[0] + planes[1]) / 2, 0.0, 0.0 }, false);
        var planeN = Unit(World(seal, new[] { 1.0, 0.0, 0.0 }, true));
        var discX = Unit(World(disc, new[] { 1.0, 0.0, 0.0 }, true));
        Near("closed seal normal / flow", Math.Abs(Dot(planeN, flowU)), 1, 1e-8);
        Near("closed disc normal / flow", Math.Abs(Dot(discX, flowU)), 1, 1e-8);
        Near("shaft parallel to seal midplane", Math.Abs(Dot(planeN, shaftU)), 0, 1e-8);
        double axial = Math.Abs(Dot(Sub(shaftP, planeP), planeN)) * 1000;
        double eccentric = LineDistance(flowP, flowU, shaftP, shaftU);
        var cone = SealCone(c.Part("09密封圈")); var coneU = Unit(World(seal, cone.Skip(3).Take(3).ToArray(), true));
        double phi = Degrees(Math.Acos(Math.Min(1, Math.Abs(Dot(coneU, flowU)))));
        double alpha = 2 * Degrees(cone[7]), bm = Math.Abs(planes[1] - planes[0]) * 1000, ds = 2 * sp[6] * 1000;
        report["c_mm"] = axial; report["e_mm"] = eccentric; report["phi_deg"] = phi; report["alpha_deg"] = alpha; report["bm_mm"] = bm; report["ds_mm"] = ds;
        report["Dmax_plane_verified"] = true; report["seal_cone_params_SI"] = cone;
        Near("c_mm", axial, d.c_mm, GeometryToleranceMm); Near("e_mm", eccentric, d.e_mm, GeometryToleranceMm);
        Near("phi_deg", phi, d.phi_deg, AngleToleranceDeg); Near("alpha_deg", alpha, d.alpha_deg, AngleToleranceDeg);
        Near("bm_mm", bm, d.bm_mm, GeometryToleranceMm); Near("ds_mm", ds, d.ds_mm, GeometryToleranceMm);
        return report;
    }
    static List<object> AuditAssembly(Context c, Design d)
    {
        string path = Directory.GetFiles(c.Folder, "*.SLDASM").Single();
        Stage(c, "open_assembly_start", path);
        c.Assembly = Open(c.App, path, 2, false, AssemblyConfiguration);
        Stage(c, "open_assembly_complete", path);
        if (!String.Equals(c.Assembly.ConfigurationManager.ActiveConfiguration.Name, AssemblyConfiguration, StringComparison.Ordinal))
        {
            c.Assembly.ShowConfiguration2(AssemblyConfiguration);
            if (!String.Equals(c.Assembly.ConfigurationManager.ActiveConfiguration.Name, AssemblyConfiguration, StringComparison.Ordinal))
                throw new InvalidOperationException("Assembly configuration was not activated: " + AssemblyConfiguration);
        }
        References(c.Assembly, c.Folder);
        ValidateFixedEndcaps(c.Assembly);
        BindCorePartsFromAssembly(c);
        var angle = Dimension(c.Assembly, "D1@角度2"); var states = new List<object>();
        // Opening is intentionally not parameterized. Rewriting an already-45°
        // mate dimension causes SolidWorks to re-solve topology-sensitive mates.
        // Read and verify the saved configuration without assigning to it.
        Near("saved opening angle", Degrees(angle.SystemValue), 45.0, AngleToleranceDeg);
        // Do not force or save the assembly in the same SolidWorks document
        // session that rebuilt the external parts. That session can report
        // transient dangling mates even though a clean reload resolves them.
        // The authoritative zero-error gate runs after CloseFolder/reopen below.
        states.Add(new { requested_angle_deg = 45.0, actual_angle_deg = Degrees(angle.SystemValue),
            geometry = AssemblyGeometry(c, d, false), feature_errors = FeatureErrors(c.Assembly), completed = true,
            policy = "fixed 45-degree configuration; first-session mate errors are diagnostic only, final clean reload is authoritative" });
        return states;
    }


    static object LocalGeometry(Context c, Design d)
    {
        var bp = BodyShaft(c.Part("03阀体")); var fp = FlowAxis(c.Part("03阀体"));
        var sp = MainShaft(c.Part("04阀轴")); var cp = SealCone(c.Part("09密封圈")); var planes = SealPlanes(c.Part("09密封圈"));
        double axial = Math.Abs(bp[0] - (planes[0] + planes[1]) / 2) * 1000;
        double eccentric = LineDistance(bp.Take(3).ToArray(), bp.Skip(3).Take(3).ToArray(), fp.Take(3).ToArray(), fp.Skip(3).Take(3).ToArray());
        double phi = Degrees(Math.Acos(Math.Min(1, Math.Abs(Dot(Unit(cp.Skip(3).Take(3).ToArray()), Unit(fp.Skip(3).Take(3).ToArray()))))));
        double alpha = 2 * Degrees(cp[7]), bm = Math.Abs(planes[1] - planes[0]) * 1000, ds = 2 * sp[6] * 1000;
        Near("reopened local c", axial, d.c_mm, GeometryToleranceMm); Near("reopened local e", eccentric, d.e_mm, GeometryToleranceMm);
        Near("reopened local phi", phi, d.phi_deg, AngleToleranceDeg); Near("reopened local alpha", alpha, d.alpha_deg, AngleToleranceDeg);
        Near("reopened local bm", bm, d.bm_mm, GeometryToleranceMm); Near("reopened local ds", ds, d.ds_mm, GeometryToleranceMm);
        return new { c_mm = axial, e_mm = eccentric, phi_deg = phi, alpha_deg = alpha, bm_mm = bm, ds_mm = ds,
            reference = "reopened part-local analytic geometry; global closed assembly alignment was separately checked before saving" };
    }

    static object Apply(Context c, Design design)
    {
        var report = O(); report["input"] = design; report["started_utc"] = DateTime.UtcNow.ToString("o");
        report["completed"] = false; report["physical_mapping_verified"] = false; report["training_ready"] = false;
        report["folder"] = c.Folder; report["writes"] = c.Writes; report["diameter_solve_trace"] = c.SolveTrace;
        report["fixed_radial_difference_mm"] = RadialDifferenceMm;
        report["Dmax_definition"] = "Maximum caliper diameter of the disc solid projected onto the closed flow-normal plane.";
        try
        {
            design.Validate();
            Stage(c, "basic_dimension_mapping_start", design);
            SetBasic(c, design);
            CaliperResult measured; double s = SolveConstruction(c, design, out measured);
            report["internal_s_mm"] = s; report["caliper_before_save"] = measured;
            var changedPartFiles = new HashSet<string>(
                c.Writes.Select(x => (string)x.file), StringComparer.OrdinalIgnoreCase);
            foreach (string file in changedPartFiles)
            {
                var part = c.Parts[file];
                Stage(c, "save_part_start", part.GetTitle()); Save(part); Stage(c, "save_part_complete", part.GetTitle());
            }
            report["saved_changed_part_files"] = changedPartFiles.OrderBy(x => x).ToArray();
            report["fixed_endcaps_saved"] = false;
            Stage(c, "close_core_parts_before_assembly", c.Folder);
            CloseFolder(c.App, c.Folder); c.Parts.Clear(); c.Assembly = null;
            report["angle_sweep"] = AuditAssembly(c, design);
            report["references"] = References(c.Assembly, c.Folder);
            report["fixed_endcaps_verified"] = true;
            report["fixed_endcap_policy"] = "Caps 1-4 are excluded from part parameterization and are restored by the v6 template assembly relationships after the six core parts are updated.";
            // Save only parts whose dimensions were explicitly changed.  The fixed
            // sealing caps are assembly fixtures, not optimization variables; saving
            // every resolved component can persist unintended in-context rebuilds and
            // destroy the closed Flow domain.
            report["assembly_saved_in_first_session"] = false;
            var expected = c.Writes.GroupBy(x => x.file + "|" + x.parameter).Select(x => x.Last()).ToArray();
            Stage(c, "close_before_reopen", c.Folder);
            CloseFolder(c.App, c.Folder); c.Assembly = null; c.Parts.Clear();
            var readback = new List<object>(); report["persisted_readback"] = readback;
            try
            {
                c.App.DocumentVisible(false, 1);
                foreach (string path in Directory.GetFiles(c.Folder, "*.SLDPRT")
                    .Where(p => !Path.GetFileName(p).StartsWith("~$", StringComparison.OrdinalIgnoreCase))
                    .Where(p => !Path.GetFileName(p).StartsWith("封盖", StringComparison.OrdinalIgnoreCase)))
                {
                    Stage(c, "reopen_part_start", Path.GetFileName(path));
                    c.Parts.Add(Path.GetFileName(path), Open(c.App, path, 1, true, ""));
                    Stage(c, "reopen_part_complete", Path.GetFileName(path));
                }
            }
            finally { c.App.DocumentVisible(true, 1); }
            bool usesGlobals = UsesGlobalVariables(c);
            foreach (var w in expected)
            {
                // V7 writes globals and V6 writes plain dimensions, so read back whichever was
                // actually written; w.parameter is a global name in the first case and a
                // dimension name in the second.
                double value = usesGlobals
                    ? GlobalValueOf(c.Parts[w.file], w.parameter)
                    : Dimension(c.Parts[w.file], w.parameter).SystemValue;
                Near("persisted " + w.file + "/" + w.parameter, value, w.requested_SI, DimensionToleranceSI);
                readback.Add(new { file = w.file, parameter = w.parameter, value_SI = value, expected_SI = w.requested_SI });
            }
            foreach (var part in c.Parts.Values)
                if (FeatureErrors(part).Count != 0) throw new InvalidOperationException("Feature error after reopen: " + part.GetTitle());
            Stage(c, "caliper_after_reopen_start", null);
            var reopenedCaliper = MeasureCaliper(c.Part("11蝶板"));
            Stage(c, "caliper_after_reopen_complete", new { lower_mm = reopenedCaliper.lower_mm, upper_mm = reopenedCaliper.upper_mm, directions = reopenedCaliper.directions });
            report["caliper_after_reopen"] = reopenedCaliper;
            report["analytic_geometry_after_reopen"] = LocalGeometry(c, design);
            if (!DiameterMatches(reopenedCaliper, design.Dmax_mm)) throw new InvalidOperationException("Reopened physical Dmax differs from the target.");
            Stage(c, "reopen_assembly_start", null);
            c.Assembly = Open(c.App, Directory.GetFiles(c.Folder, "*.SLDASM").Single(), 2, true, AssemblyConfiguration);
            Stage(c, "reopen_assembly_complete", null);
            report["lock_release"] = ReleaseOverConstrainingLocks(c.Assembly);
            // Read the mate errors only after a forced rebuild: a freshly opened
            // assembly still carries the stored (clean) error codes, so checking
            // before rebuilding would pass even when the mates are violated.
            Rebuild(c.Assembly);
            report["reopened_references"] = References(c.Assembly, c.Folder);
            ValidateFixedEndcaps(c.Assembly);
            report["reopened_fixed_endcaps_verified"] = true;
            if (FeatureErrors(c.Assembly).Count != 0)
                throw new InvalidOperationException("Assembly feature errors remain after reopen.");
            Near("reopened opening angle", Degrees(Dimension(c.Assembly, "D1@角度2").SystemValue), 45, AngleToleranceDeg);
            report["reopened_axis"] = AssemblyGeometry(c, design, false);
            Stage(c, "create_capless_output_start", null);
            report["capless_output"] = CreateCaplessOutput(c);
            Stage(c, "create_capless_output_complete", report["capless_output"]);
            report["cad_hashes_after"] = Directory.GetFiles(c.Folder, "*.SLD*")
                .Where(p => !Path.GetFileName(p).StartsWith("~$", StringComparison.OrdinalIgnoreCase))
                .Select(p => new { file = Path.GetFileName(p), sha256 = Hash(p) }).ToArray();
            report["physical_mapping_verified"] = true; report["completed"] = true;
        }
        catch (Exception ex) { report["error"] = ex.ToString(); }
        Stage(c, (bool)report["completed"] ? "trial_completed" : "trial_failed", report.ContainsKey("error") ? report["error"] : null);
        report["finished_utc"] = DateTime.UtcNow.ToString("o");
        return report;
    }

    /// <summary>Apply one design to an already isolated trial folder. Never operates on the source/template.</summary>
    public static object ApplyAndMeasure(string root, string isolatedFolder, string sevenInputsJson)
    {
        root = RootPath(root); isolatedFolder = Under(isolatedFolder, Path.Combine(root, "working", "seven_variable_trials"), true);
        var values = Serializer().Deserialize<Dictionary<string, object>>(sevenInputsJson);
        var names = new[] { "c_mm", "e_mm", "phi_deg", "alpha_deg", "Dmax_mm", "bm_mm", "ds_mm" };
        if (values.Count != names.Length || names.Any(n => !values.ContainsKey(n))) throw new ArgumentException("Exactly seven named input fields are required.");
        if (values.Values.Any(x => x is bool || x is string || x == null)) throw new ArgumentException("Inputs must be JSON numbers.");
        var design = Serializer().Deserialize<Design>(sevenInputsJson); design.Validate();
        var app = Connect();
        Context context = null;
        try { context = new Context(app, root, isolatedFolder); return Apply(context, design); }
        finally { if (context != null) context.Dispose();  }
    }


    static object ProbeMaterial(SW.IModelDoc2 doc)
    {
        var row = O(); row["file"] = Path.GetFileName(doc.GetPathName());
        string config = doc.ConfigurationManager.ActiveConfiguration.Name; row["configuration"] = config;
        try { string database; row["part_material_name"] = ((SW.IPartDoc)doc).GetMaterialPropertyName2(config, out database); row["part_database"] = database; }
        catch (Exception ex) { row["part_material_read_error"] = ex.Message; }
        var bodies = new List<object>(); row["bodies"] = bodies;
        foreach (var body in Bodies(doc))
        {
            var br = O(); br["body_name"] = body.Name; bodies.Add(br);
            try { string database; br["material_name"] = body.GetMaterialPropertyName(config, out database); br["database"] = database; }
            catch (Exception ex) { br["material_read_error"] = ex.Message; }
        }
        row["source"] = "actual part/body material binding API; not appearance colors";
        return row;
    }

    static Design ReadTemplateDesign(SW.ISldWorks app, string folder, out CaliperResult caliper, out List<object> materials, string progressFolder)
    {
        materials = new List<object>();
        string[] paths = Directory.GetFiles(folder, "*.SLD*");
        RefuseSameNamedOpenDocuments(app, paths);
        var parts = new Dictionary<string, SW.IModelDoc2>();
        try
        {
            foreach (string path in Directory.GetFiles(folder, "*.SLDPRT"))
            {
                WriteStage(progressFolder, "template_open_part_start", Path.GetFileName(path));
                var part = Open(app, path, 1, true, ""); parts[Path.GetFileName(path)] = part;
                WriteStage(progressFolder, "template_material_probe", Path.GetFileName(path)); materials.Add(ProbeMaterial(part));
                WriteStage(progressFolder, "template_open_part_complete", Path.GetFileName(path));
            }
            var seal = parts.Single(x => x.Key.Contains("09密封圈")).Value;
            var shaft = parts.Single(x => x.Key.Contains("04阀轴")).Value;
            var disc = parts.Single(x => x.Key.Contains("11蝶板")).Value;
            WriteStage(progressFolder, "template_caliper_start", null);
            caliper = MeasureCaliper(disc);
            WriteStage(progressFolder, "template_caliper_complete", new { lower_mm = caliper.lower_mm, upper_mm = caliper.upper_mm, directions = caliper.directions });
            double s = Dimension(disc, "D3@草图3").SystemValue * 1000;
            double radius = Dimension(disc, "D5@草图3").SystemValue * 1000;
            Near("template radial construction difference", s / 2 - radius, RadialDifferenceMm, 1e-6);
            var d = new Design {
                c_mm = Dimension(seal, "D3@草图1").SystemValue * 1000,
                e_mm = Dimension(seal, "D9@草图1").SystemValue * 1000,
                phi_deg = Degrees(Dimension(seal, "D5@草图1").SystemValue),
                alpha_deg = 2 * Degrees(Dimension(seal, "D6@草图1").SystemValue),
                Dmax_mm = caliper.midpoint_mm,
                bm_mm = Dimension(seal, "D2@草图1").SystemValue * 1000,
                ds_mm = Dimension(shaft, "D4@草图1").SystemValue * 1000
            };
            d.Validate(); return d;
        }
        finally { CloseFolder(app, folder); }
    }
    static List<Tuple<string, Design>> Trials(Design baseline, bool fullSuite)
    {
        var trials = new List<Tuple<string, Design>>(); trials.Add(Tuple.Create("baseline", baseline.Copy()));
        if (fullSuite)
        {
            string[] fields = { "c_mm", "e_mm", "phi_deg", "alpha_deg", "Dmax_mm", "bm_mm", "ds_mm" };
            double[] steps = { .32, .037, .1, .1, baseline.Dmax_mm * .01, .075, .45 };
            for (int i = 0; i < fields.Length; i++)
                foreach (int sign in new[] { -1, 1 })
                {
                    var d = baseline.Copy(); var field = typeof(Design).GetField(fields[i]);
                    field.SetValue(d, (double)field.GetValue(d) + sign * steps[i]);
                    trials.Add(Tuple.Create(fields[i] + (sign < 0 ? "_minus" : "_plus"), d));
                }
        }
        else
        {
            foreach (int sign in new[] { -1, 1 })
            {
                var d = baseline.Copy();
                d.c_mm += sign * .16; d.e_mm += sign * .02; d.phi_deg += sign * .05; d.alpha_deg += sign * .1;
                d.Dmax_mm *= 1 + sign * .005; d.bm_mm += sign * .05; d.ds_mm += sign * .1;
                trials.Add(Tuple.Create(sign < 0 ? "joint_minus" : "joint_plus", d));
            }
        }
        foreach (var t in trials) t.Item2.Validate();
        return trials;
    }
    /// <summary>Create independent CAD-only copies and execute either 3 joint trials or 15 geometric trials.</summary>
    public static object Run(string root, string templateFolder, string runName, bool fullSuite)
    {
        return RunSuite(root, templateFolder, runName, fullSuite ? "full" : "three");
    }
    public static object RunSuite(string root, string templateFolder, string runName, string suite)
    {
        if (suite != "baseline" && suite != "three" && suite != "full") throw new ArgumentException("Unknown geometry suite.");
        bool fullSuite = suite == "full";
        int expectedTrials = fullSuite ? 15 : (suite == "baseline" ? 1 : 3);
        root = RootPath(root); templateFolder = Under(templateFolder, Path.Combine(root, "working"), true);
        if (String.IsNullOrEmpty(runName) || runName.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || runName == "." || runName == "..")
            throw new ArgumentException("A plain unique run name is required.");
        string batch = Under(Path.Combine(root, "working", "seven_variable_trials", runName), Path.Combine(root, "working", "seven_variable_trials"), false);
        if (Directory.Exists(batch)) throw new IOException("Refusing to overwrite an existing trial batch: " + batch);
        var sourceFiles = Directory.GetFiles(templateFolder, "*.SLD*").OrderBy(x => x).ToArray();
        if (sourceFiles.Count(p => p.EndsWith(".SLDASM", StringComparison.OrdinalIgnoreCase)) != 1 ||
            sourceFiles.Count(p => p.EndsWith(".SLDPRT", StringComparison.OrdinalIgnoreCase)) != 14)
            throw new InvalidOperationException("Template must contain the sealed-flow v5 assembly and fourteen external parts.");
        foreach (string source in sourceFiles) Under(source, templateFolder, true);
        var initialHashes = sourceFiles.ToDictionary(p => Path.GetFileName(p), Hash);
        Directory.CreateDirectory(batch);
        ProgressWriteWarnings.Clear();
        var results = new List<object>(); var report = O();
        report["adapter_version"] = "physical_dmax_v2_1_adaptive"; report["run_name"] = runName; report["template_folder"] = templateFolder;
        report["folder"] = batch; report["started_utc"] = DateTime.UtcNow.ToString("o"); report["status"] = "running";
        report["training_ready"] = false; report["simulation_labels_generated"] = false; report["results"] = results;
        report["template_hashes_before"] = initialHashes;
        report["progress_write_warnings"] = ProgressWriteWarnings;
        string artifact = Under(Path.Combine(root, "artifacts", "seven_variable_mapping_" + runName + ".json"), Path.Combine(root, "artifacts"), false);
        var app = Connect(); report["revision"] = app.RevisionNumber();
        bool originalCommandInProgress = app.CommandInProgress;
        report["command_in_progress_original"] = originalCommandInProgress;
        try
        {
            app.CommandInProgress = true;
            report["command_in_progress_during"] = app.CommandInProgress;
            WriteStage(batch, "template_probe_start", new { template = templateFolder, command_in_progress_original = originalCommandInProgress });
            CaliperResult baselineCaliper; List<object> materials; var baseline = ReadTemplateDesign(app, templateFolder, out baselineCaliper, out materials, batch);
            report["template_material_bindings"] = materials;
            report["baseline_input"] = baseline; report["template_disc_caliper"] = baselineCaliper;
            report["baseline_Dmax_source"] = "Midpoint of measured physical caliper bounds; not the stored cone-circle diameter.";
            report["suite"] = suite; report["requested_trials"] = expectedTrials;
            foreach (var trial in Trials(baseline, fullSuite).Take(expectedTrials))
            {
                string folder = Under(Path.Combine(batch, trial.Item1), batch, false);
                Directory.CreateDirectory(folder);
                foreach (string source in sourceFiles)
                {
                    string target = Under(Path.Combine(folder, Path.GetFileName(source)), folder, false);
                    File.Copy(source, target, false);
                    if (Hash(target) != initialHashes[Path.GetFileName(source)]) throw new IOException("Clone hash mismatch.");
                }
                Context context = null;
                var row = O(); row["id"] = trial.Item1; row["folder"] = folder; results.Add(row);
                try
                {
                    WriteStage(batch, "trial_start", new { id = trial.Item1, folder = folder });
                    context = new Context(app, root, folder);
                    row["result"] = Apply(context, trial.Item2);
                }
                catch (Exception ex) { row["result"] = new { completed = false, physical_mapping_verified = false, training_ready = false, error = ex.ToString() }; }
                finally { if (context != null) context.Dispose();  }
                WriteStage(batch, "trial_finished", new { id = trial.Item1, folder = folder });
                WriteJson(Path.Combine(folder, "mapping_result.json"), row);
                WriteJson(Path.Combine(batch, "mapping_report.json"), report); WriteJson(artifact, report);
            }
            var finalHashes = sourceFiles.ToDictionary(p => Path.GetFileName(p), Hash);
            bool unchanged = initialHashes.All(x => finalHashes[x.Key] == x.Value);
            report["template_hashes_after"] = finalHashes; report["template_unchanged"] = unchanged;
            if (!unchanged) throw new InvalidOperationException("Template hashes changed during execution; batch cannot be accepted.");
            report["status"] = "execution_finished";
        }
        catch (Exception ex) { report["status"] = "execution_failed"; report["error"] = ex.ToString(); }
        finally
        {
            try
            {
                app.CommandInProgress = originalCommandInProgress;
                report["command_in_progress_restored"] = app.CommandInProgress == originalCommandInProgress;
                if (!(bool)report["command_in_progress_restored"]) report["status"] = "execution_failed";
            }
            catch (Exception restoreError) { report["command_state_restore_error"] = restoreError.Message; report["status"] = "execution_failed"; }
        }
        report["finished_utc"] = DateTime.UtcNow.ToString("o");
        report["completed_trials"] = results.Cast<Dictionary<string, object>>().Count(row =>
        {
            var data = Serializer().Deserialize<Dictionary<string, object>>(Serializer().Serialize(row["result"]));
            return data.ContainsKey("completed") && data["completed"] is bool && (bool)data["completed"];
        });
        report["all_requested_geometry_trials_passed"] = (string)report["status"] == "execution_finished" &&
            (int)report["completed_trials"] == expectedTrials;
        WriteJson(Path.Combine(batch, "mapping_report.json"), report); WriteJson(artifact, report);
        return report;
    }
}
