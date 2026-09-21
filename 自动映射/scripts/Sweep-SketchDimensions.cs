// Compiled twin of Sweep-SketchDimensions.ps1 (this machine's execution policy blocks .ps1
// files, and csc + exe is the pattern scripts/Inspect012Mates.exe already uses).
//
// Read-only in effect: perturbs one sketch dimension at a time in memory, rebuilds, snapshots
// every face, then restores the dimension.  The document is NEVER saved.
//
// Why: on the disc, `c` is written to exactly one dimension of the revolve-profile sketch and
// only the sealing cone follows it - the plate faces stay pinned, so the part is sheared
// instead of translated and the gasket and the seat ring both cut into it.  This probe answers
// the missing half of the question: which dimension of that sketch positions each face?
//
// Attach order matters: .NET's Marshal.GetActiveObject only connects once a document is already
// open, so open the part with scripts/open_part.py first, then run this exe.
//
// Usage: Sweep-SketchDimensions.exe <output.json> [sketchName] [deltaMm] [maxIndex]
// ASCII only - the default sketch name is built from code points.
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class SketchDimSweep
{
    static object[] A(object x) { return x as object[] ?? new object[0]; }
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }
    static double N(Func<double> f) { try { return f(); } catch { return 0; } }

    // body bounding box in mm - the ONLY reliable outline signal.  A face diff cannot see a
    // cylinder or a coaxial cone move along its own axis (the surface is bit-identical), so
    // "no face moved" has repeatedly meant "this probe cannot see it", not "nothing happened".
    static double[] Box(SW.IModelDoc2 doc)
    {
        try
        {
            var pd = doc as SW.IPartDoc; if (pd == null) return null;
            var bodies = pd.GetBodies2(0, false) as object[];
            if (bodies == null || bodies.Length == 0) return null;
            var b = bodies[0] as SW.IBody2; if (b == null) return null;
            var box = b.GetBodyBox() as double[];
            if (box == null || box.Length != 6) return null;
            var o = new double[6];
            for (int i = 0; i < 6; i++) o[i] = Math.Round(box[i] * 1000, 4);
            return o;
        }
        catch { return null; }
    }

    // x of every plane whose normal is along +/-X: the only faces a face-diff can classify
    static double[] XPlanes(SW.IModelDoc2 doc)
    {
        var xs = new List<double>();
        try
        {
            foreach (var body in A(((SW.IPartDoc)doc).GetBodies2(0, false)))
                foreach (var x in A(((SW.IBody2)body).GetFaces()))
                {
                    var s = ((SW.IFace2)x).GetSurface() as SW.ISurface;
                    if (s == null || !s.IsPlane()) continue;
                    var p = (double[])s.PlaneParams;
                    if (Math.Abs(p[0]) > 0.999) xs.Add(Math.Round(p[3] * 1000, 4));
                }
        }
        catch { }
        xs.Sort();
        return xs.ToArray();
    }

    // Volume and centre of mass.  A rigid translation preserves the volume exactly and moves the
    // centroid by exactly that vector; a part that is stretched or sheared does neither.  This is
    // the only check here that does not depend on which faces happen to be planar.
    static object Stats(SW.IModelDoc2 doc)
    {
        try
        {
            var ext = doc.Extension;
            var mp = ext.CreateMassProperty();
            if (mp == null) return null;
            try { mp.UseSystemUnits = true; } catch { }
            double vol; try { vol = mp.Volume * 1e9; } catch {  return null; }
            double[] com = null;
            try { com = mp.CenterOfMass as double[]; } catch { }
            
            return new Dictionary<string, object>{
                {"volume_mm3", Math.Round(vol, 3)},
                {"centroid_mm", com == null ? null : new[]{Math.Round(com[0]*1000,4),Math.Round(com[1]*1000,4),Math.Round(com[2]*1000,4)}}};
        }
        catch { return null; }
    }

    // EVERY face with its own bounding box, including the surface types the old dump skipped
    // (sphere / torus / bspline).  Those are precisely the ones that can set the axial extreme
    // of a part whose outline is an arc rather than a plane, so leaving them out hid the answer.
    static object[] FaceBoxes(SW.IModelDoc2 doc)
    {
        var rows = new List<object>();
        var pd = doc as SW.IPartDoc; if (pd == null) return rows.ToArray();
        int i = 0;
        foreach (var b in A(pd.GetBodies2(0, false)))
            foreach (var x in A(((SW.IBody2)b).GetFaces()))
            {
                var f = (SW.IFace2)x;
                var s = f.GetSurface() as SW.ISurface;
                string kind = "other";
                try
                {
                    if (s != null)
                    {
                        if (s.IsPlane()) kind = "plane";
                        else if (s.IsCone()) kind = "cone";
                        else if (s.IsCylinder()) kind = "cylinder";
                        else if (s.IsSphere()) kind = "sphere";
                        else if (s.IsTorus()) kind = "torus";
                        else kind = "bspline/other";
                    }
                }
                catch { }
                object box = null;
                try
                {
                    var fb = f.GetBox() as double[];
                    if (fb != null && fb.Length == 6)
                        box = new[]{ Math.Round(fb[0]*1000,4), Math.Round(fb[3]*1000,4),
                                     Math.Round(Math.Abs(f.GetArea())*1e6,2) };
                }
                catch { }
                rows.Add(new { i = i++, kind = kind, x = box });
            }
        return rows.ToArray();
    }

    // Every cone face with its radius and reference point.  This is the signal a volume/box diff
    // misses: a dimension that slides a construction along the cone barely changes either, but a
    // diameter-型 dimension moves the cone's radius by half the step.  Recorded per sweep row so
    // "which dimension actually sets the seat cone" is readable straight off the table.
    static object[] ConeFaces(SW.IModelDoc2 doc)
    {
        var rows = new List<object>();
        try
        {
            foreach (var body in A(((SW.IPartDoc)doc).GetBodies2(0, false)))
                foreach (var x in A(((SW.IBody2)body).GetFaces()))
                {
                    var f = (SW.IFace2)x; var s = f.GetSurface() as SW.ISurface;
                    if (s == null || !s.IsCone()) continue;
                    var q = (double[])s.ConeParams;
                    rows.Add(new Dictionary<string, object>{
                        {"R_mm", Math.Round(q[6]*1000, 4)},
                        {"ha_deg", Math.Round(q[7]*180.0/Math.PI, 3)},
                        {"pt_mm", new[]{Math.Round(q[0]*1000,4), Math.Round(q[1]*1000,4), Math.Round(q[2]*1000,4)}},
                        {"area_mm2", Math.Round(Math.Abs(f.GetArea())*1e6, 2)}});
                }
        }
        catch { }
        return rows.ToArray();
    }

    static object[] Faces(SW.IModelDoc2 doc)
    {
        var rows = new List<object>();
        var pd = doc as SW.IPartDoc; if (pd == null) return rows.ToArray();
        foreach (var b in A(pd.GetBodies2(0, false)))
            foreach (var x in A(((SW.IBody2)b).GetFaces()))
            {
                var f = (SW.IFace2)x; var s = f.GetSurface() as SW.ISurface; if (s == null) continue;
                double area = Math.Round(Math.Abs(f.GetArea()) * 1e6, 3);
                if (s.IsPlane())
                {
                    var p = (double[])s.PlaneParams;
                    rows.Add(new Dictionary<string, object>{{"kind","plane"},{"area_mm2",area},
                        {"normal",new[]{Math.Round(p[0],5),Math.Round(p[1],5),Math.Round(p[2],5)}},
                        {"point_mm",new[]{Math.Round(p[3]*1000,4),Math.Round(p[4]*1000,4),Math.Round(p[5]*1000,4)}}});
                }
                else if (s.IsCone())
                {
                    var q = (double[])s.ConeParams;
                    rows.Add(new Dictionary<string, object>{{"kind","cone"},{"area_mm2",area},
                        {"radius_mm",Math.Round(q[6]*1000,4)},
                        {"half_angle_deg",Math.Round(q[7]*180.0/Math.PI,4)},
                        {"axis",new[]{Math.Round(q[3],5),Math.Round(q[4],5),Math.Round(q[5],5)}},
                        {"point_mm",new[]{Math.Round(q[0]*1000,4),Math.Round(q[1]*1000,4),Math.Round(q[2]*1000,4)}}});
                }
                else if (s.IsCylinder())
                {
                    var q = (double[])s.CylinderParams;
                    rows.Add(new Dictionary<string, object>{{"kind","cylinder"},{"area_mm2",area},
                        {"radius_mm",Math.Round(q[6]*1000,4)},
                        {"axis",new[]{Math.Round(q[3],5),Math.Round(q[4],5),Math.Round(q[5],5)}},
                        {"point_mm",new[]{Math.Round(q[0]*1000,4),Math.Round(q[1]*1000,4),Math.Round(q[2]*1000,4)}}});
                }
            }
        return rows.ToArray();
    }

    public static int Main(string[] argv)
    {
        if (argv.Length < 1)
        {
            Console.Error.WriteLine("usage: Sweep-SketchDimensions.exe <output.json> [sketchName] [deltaMm] [maxIndex]");
            return 2;
        }
        string outPath = argv[0];
        double delta = argv.Length > 1 ? double.Parse(argv[1], System.Globalization.CultureInfo.InvariantCulture) : 2.0;
        int maxSketch = argv.Length > 2 ? int.Parse(argv[2]) : 11;
        int maxIndex = argv.Length > 3 ? int.Parse(argv[3]) : 12;

        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex)
        {
            Console.Error.WriteLine("cannot attach to SolidWorks - open a document first: " + ex.Message);
            return 3;
        }
        var app = (SW.ISldWorks)com;
        var r = new Dictionary<string, object>();
        var doc = (SW.IModelDoc2)app.ActiveDoc;
        if (doc == null) { Console.Error.WriteLine("no active document"); return 4; }

        r["title"] = S(() => doc.GetTitle());
        r["delta_mm"] = delta;
        try
        {
            doc.ForceRebuild3(false);
            r["baseline_box_mm"] = Box(doc);
            r["baseline_xplanes"] = XPlanes(doc);
            r["baseline_stats"] = Stats(doc);
            r["baseline_faceboxes"] = FaceBoxes(doc);
            r["baseline_faces"] = Faces(doc);

            var all = new Dictionary<string, object>();
            for (int s = 1; s <= maxSketch; s++)
            {
                // built from code points so this source stays pure ASCII (csc reads a BOM-less
                // .cs with the ANSI code page, so a literal would come out mangled)
                string sketch = ((char)0x8349).ToString() + ((char)0x56FE).ToString()
                                + s.ToString(System.Globalization.CultureInfo.InvariantCulture);
                bool exists = false;
                try { exists = (doc.Parameter("D1@" + sketch) as SW.IDimension) != null; } catch { }
                if (!exists) continue;

                var rows = new Dictionary<string, object>();
                for (int i = 1; i <= maxIndex; i++)
                {
                    string nm = "D" + i + "@" + sketch;
                    SW.IDimension d = null;
                    try { d = doc.Parameter(nm) as SW.IDimension; } catch { }
                    if (d == null) continue;
                    double baseV = Math.Round(N(() => d.SystemValue) * 1000, 4);
                    var row = new Dictionary<string, object>();
                    row["base_mm"] = baseV;
                    try { d.SystemValue = (baseV + delta) / 1000.0; row["set"] = true; }
                    catch (Exception ex) { row["set"] = false; row["error"] = ex.Message; rows[nm] = row; continue; }
                    doc.ForceRebuild3(false);
                    row["box_mm"] = Box(doc);
                    row["xplanes"] = XPlanes(doc);
                    row["stats"] = Stats(doc);   // volume tells a hole (down) from a boss (up)
                    row["cones"] = ConeFaces(doc);
                    row["faces"] = Faces(doc);
                    try { SW.IDimension d2 = doc.Parameter(nm) as SW.IDimension; if (d2 != null) d2.SystemValue = baseV / 1000.0; } catch { }
                    doc.ForceRebuild3(false);
                    rows[nm] = row;
                }
                if (rows.Count > 0) { all[sketch] = rows; Console.WriteLine("  " + sketch + ": " + rows.Count + " dims"); }
            }
            r["sketches"] = all;
        }
        catch (Exception ex) { r["exception"] = ex.ToString(); }

        // minimal JSON writer (no System.Web.Extensions dependency)
        File.WriteAllText(outPath, Json.Write(r), new System.Text.UTF8Encoding(false));
        Console.WriteLine("written: " + outPath);
        return 0;   // caller closes the document WITHOUT saving
    }
}

static class Json
{
    public static string Write(object o) { var sb = new System.Text.StringBuilder(); W(sb, o); return sb.ToString(); }
    static void W(System.Text.StringBuilder sb, object o)
    {
        if (o == null) { sb.Append("null"); return; }
        if (o is string) { sb.Append('"').Append(((string)o).Replace("\\", "\\\\").Replace("\"", "\\\"")
                                                          .Replace("\n", "\\n").Replace("\r", "\\r").Replace("\t", "\\t")).Append('"'); return; }
        if (o is bool) { sb.Append(((bool)o) ? "true" : "false"); return; }
        if (o is double) { sb.Append(((double)o).ToString("R", System.Globalization.CultureInfo.InvariantCulture)); return; }
        if (o is int) { sb.Append(((int)o).ToString(System.Globalization.CultureInfo.InvariantCulture)); return; }
        if (o is System.Collections.IDictionary)
        {
            sb.Append('{'); bool first = true;
            foreach (System.Collections.DictionaryEntry e in (System.Collections.IDictionary)o)
            { if (!first) sb.Append(','); first = false; W(sb, e.Key); sb.Append(':'); W(sb, e.Value); }
            sb.Append('}'); return;
        }
        if (o is System.Collections.IEnumerable)
        {
            sb.Append('['); bool first = true;
            foreach (var e in (System.Collections.IEnumerable)o) { if (!first) sb.Append(','); first = false; W(sb, e); }
            sb.Append(']'); return;
        }
        // anonymous types: reflect over the public properties (without this they fall through to
        // ToString() and the whole record arrives as an unreadable string)
        var props = o.GetType().GetProperties();
        if (props.Length > 0)
        {
            sb.Append('{'); bool first = true;
            foreach (var p in props)
            { if (!first) sb.Append(','); first = false; W(sb, p.Name); sb.Append(':'); W(sb, p.GetValue(o, null)); }
            sb.Append('}'); return;
        }
        sb.Append('"').Append(o.ToString()).Append('"');
    }
}
