// Pins 10压板's bushing anchor point to the end of its 194.37 construction line.
//
// The plate has the same class of defect as the seal ring, but not the same shape of defect:
//
//   09密封圈: the bushing CROSSED the construction line's end, but that point was not a vertex
//             of the bushing, so it slid along the bushing.  Fix = split the bushing there.
//   10压板  : the bushing does NOT cross the construction line's end at all.  Its position is set
//             by an anchor point (-4.3353, 97.8606) that merely LIES ON the construction line.
//             "Lying on" is not "carried by": when 194.37 grows, the line moves, the anchor does
//             not have to, and the solver only nudged it 0.207 mm sideways.  The bushing's
//             direction is already locked (type 7 against the second construction line), so it
//             could only move laterally - the cone never followed s.
//
// The anchor already lies on the bushing (type 9); that part needs no change.  What is missing is
// a constraint on WHERE ALONG the construction line it sits.  Measuring it to the construction
// line's end gives 1.575 mm - exactly the template's fixed radial difference Delta - which is why
// one constant dimension is enough: the end moves, the anchor is dragged with it, the bushing
// translates, the cone follows.  No formula, no split.
//
// Read-only unless --save.  Always run on a copy first.
//
// Usage: Add-PlateAnchorDim.exe <copy.SLDPRT> <out.json> [--save]
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class PlateAnchorDim
{
    // the two points, in mm, as read off 10压板 草图1: the anchor and the construction line's end
    const double AnchorX = -4.3353, AnchorY = 97.8606;
    const double LineEndX = -4.1093, LineEndY = 99.4193;
    const double TolMm = 0.05;

    static object[] A(object x) { return x as object[] ?? new object[0]; }
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }
    static double N(Func<double> f) { try { return f(); } catch { return 0; } }
    static object R(double v) { return Math.Round(v, 3); }

    public static int Main(string[] argv)
    {
        if (argv.Length < 2) { Console.Error.WriteLine("usage: Add-PlateAnchorDim.exe <copy.SLDPRT> <out.json> [--save]"); return 2; }
        string part = argv[0], outPath = argv[1];
        bool save = false, replace = false;
        for (int i = 2; i < argv.Length; i++) { if (argv[i] == "--save") save = true; if (argv[i] == "--replace") replace = true; }

        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex) { Console.Error.WriteLine("cannot attach: " + ex.Message); return 3; }
        var app = (SW.ISldWorks)com;

        string target = Path.GetFileNameWithoutExtension(part);
        foreach (var o in A(app.GetDocuments()))
        {
            var d = o as SW.IModelDoc2; if (d == null) continue;
            if (string.Equals(S(() => Path.GetFileNameWithoutExtension(d.GetPathName())), target, StringComparison.OrdinalIgnoreCase))
            { Console.Error.WriteLine("refusing: '" + target + "' is already open"); return 5; }
        }
        Console.WriteLine("gate: revision=" + S(() => app.RevisionNumber()) + " visible=" + S(() => app.Visible.ToString()));

        int err = 0, warn = 0;
        var doc = (SW.IModelDoc2)app.OpenDoc6(part, 1, 0, "", ref err, ref warn);
        if (doc == null) { Console.Error.WriteLine("OpenDoc6 failed err=" + err); return 7; }

        var r = new Dictionary<string, object>();
        r["part"] = part; r["save"] = save; r["replace"] = replace;
        try
        {
            doc.ForceRebuild3(false);
            object[] ptsB = null, ptsA = null;
            r["before"] = Snapshot(doc, out ptsB);

            // find the sketch that owns BOTH points
            SW.ISketch sk = null; SW.IFeature feat = null;
            for (var f = (SW.IFeature)doc.FirstFeature(); f != null; f = (SW.IFeature)f.GetNextFeature())
            {
                var s2 = f.GetSpecificFeature2() as SW.ISketch;
                if (s2 == null) continue;
                double d1, d2;
                if (FindPoint(s2, AnchorX, AnchorY, out d1) != null && FindPoint(s2, LineEndX, LineEndY, out d2) != null)
                { sk = s2; feat = f; break; }
            }
            r["sketch"] = feat == null ? null : feat.Name;
            if (sk == null) { r["error"] = "no sketch owns both the anchor and the construction line end"; }
            else
            {
                doc.ClearSelection2(true);
                feat.Select2(false, 0);
                doc.EditSketch();
                var sk2 = doc.SketchManager.ActiveSketch ?? sk;

                double da = 0, db = 0;
                var anchor = FindPoint(sk2, AnchorX, AnchorY, out da);
                var lineEnd = FindPoint(sk2, LineEndX, LineEndY, out db);
                r["anchor_found"] = anchor != null;
                r["line_end_found"] = lineEnd != null;
                if (anchor == null || lineEnd == null) { r["error"] = "points not found in the active sketch"; }
                else
                {
                    // the distance they are about to be dimensioned by - should come out 1.575
                    r["current_distance_mm"] = Math.Round(Math.Sqrt(
                        Math.Pow(anchor.X*1000 - lineEnd.X*1000, 2) + Math.Pow(anchor.Y*1000 - lineEnd.Y*1000, 2)), 4);

                    // D8 = 95.61 ties the anchor to a FIXED point (the junction of the two fixed
                    // construction lines), which is what stops the bushing following s: the anchor
                    // inherits only the construction line's perpendicular offset (0.2045 mm) instead
                    // of the large along-the-line motion of its end point.  Re-point it at the end.
                    if (replace)
                    {
                        for (var f2 = (SW.IFeature)doc.FirstFeature(); f2 != null; f2 = (SW.IFeature)f2.GetNextFeature())
                        {
                            if (f2.GetSpecificFeature2() as SW.ISketch == null) continue;
                            object dsp = null; try { dsp = f2.GetFirstDisplayDimension(); } catch { }
                            int g2 = 0;
                            while (dsp != null && g2++ < 200)
                            {
                                try
                                {
                                    var dd2 = dsp as SW.IDisplayDimension;
                                    var d2 = dd2 == null ? null : dd2.GetDimension2(0) as SW.IDimension;
                                    if (d2 != null && Math.Abs(d2.SystemValue * 1000.0 - 95.61) < 0.005)
                                    {
                                        string nm = S(() => d2.FullName);
                                        r["removing_dim"] = nm;
                                        r["removed"] = doc.Extension.SelectByID2(nm, "DIMENSION", 0, 0, 0, false, 0, null, 0);
                                        doc.EditDelete();
                                        dsp = null; break;
                                    }
                                }
                                catch { }
                                try { dsp = f2.GetNextDisplayDimension(dsp); } catch { dsp = null; }
                            }
                            if (r.ContainsKey("removing_dim")) break;
                        }
                        sk2 = doc.SketchManager.ActiveSketch ?? sk2;
                        anchor = FindPoint(sk2, AnchorX, AnchorY, out da);
                        lineEnd = FindPoint(sk2, LineEndX, LineEndY, out db);
                        doc.ClearSelection2(true);
                        r["points_after_delete"] = new[]{ anchor != null, lineEnd != null };
                    }
                    doc.ClearSelection2(true);
                    bool s1 = anchor != null && anchor.Select4(false, null);
                    bool s2b = lineEnd != null && lineEnd.Select4(true, null);
                    r["selected"] = new[]{ s1, s2b };

                    // place the dimension just below the anchor; its value is created from the
                    // current geometry, so no value has to be typed and none is invented
                    var disp = doc.AddDimension2(AnchorX / 1000.0, (AnchorY - 6.0) / 1000.0, 0.0);
                    var dd = disp as SW.IDisplayDimension;
                    var dim = dd == null ? null : dd.GetDimension2(0) as SW.IDimension;
                    r["dim_created"] = dim != null;
                    r["dim_name"] = dim == null ? null : S(() => dim.FullName);
                    r["dim_value_mm"] = dim == null ? null : (object)Math.Round(N(() => dim.SystemValue) * 1000, 4);
                }

                doc.SketchManager.InsertSketch(true);
                doc.ClearSelection2(true);
                doc.EditRebuild3();
                doc.ForceRebuild3(false);
                r["after"] = Snapshot(doc, out ptsA);

                var moves = new List<object>();
                if (ptsB != null && ptsA != null)
                    for (int i = 0; i < ptsB.Length && i < ptsA.Length; i++)
                    {
                        var p0 = ptsB[i] as double[]; var p1 = ptsA[i] as double[];
                        if (p0 == null || p1 == null) continue;
                        if (Math.Abs(p1[0]-p0[0]) < 1e-4 && Math.Abs(p1[1]-p0[1]) < 1e-4) continue;
                        moves.Add(new Dictionary<string, object>{
                            {"from", new[]{ R(p0[0]), R(p0[1]) }}, {"to", new[]{ R(p1[0]), R(p1[1]) }}});
                    }
                r["moved_points"] = moves.ToArray();
                if (save) { int e2 = 0, w2 = 0; r["saved"] = doc.Save3(1, ref e2, ref w2); r["save_err"] = e2; }
            }
        }
        catch (Exception ex) { r["exception"] = ex.ToString(); }

        try { doc.ClearSelection2(true); if (!save) doc.SetSaveFlag(); app.CloseDoc(doc.GetTitle()); } catch { }
        File.WriteAllText(outPath, Json.Write(r), new System.Text.UTF8Encoding(false));
        Console.WriteLine("written: " + outPath);
        return 0;
    }

    static SW.ISketchPoint FindPoint(SW.ISketch sk, double x, double y, out double dist)
    {
        dist = double.MaxValue;
        try
        {
            SW.ISketchPoint bp = null;
            foreach (var o in A(sk.GetSketchPoints2()))
            {
                var p = o as SW.ISketchPoint; if (p == null) continue;
                double d = Math.Sqrt(Math.Pow(p.X*1000-x, 2) + Math.Pow(p.Y*1000-y, 2));
                if (d < dist) { dist = d; bp = p; }
            }
            return (bp != null && dist <= TolMm) ? bp : null;
        }
        catch { return null; }
    }

    static object Snapshot(SW.IModelDoc2 doc, out object[] pts)
    {
        var d = new Dictionary<string, object>();
        var all = new List<object>();
        try
        {
            for (var f = (SW.IFeature)doc.FirstFeature(); f != null; f = (SW.IFeature)f.GetNextFeature())
            {
                var sk = f.GetSpecificFeature2() as SW.ISketch;
                if (sk == null) continue;
                foreach (var o in A(sk.GetSketchPoints2()))
                {
                    var p = o as SW.ISketchPoint; if (p == null) continue;
                    all.Add(new double[]{ Math.Round(p.X*1000, 3), Math.Round(p.Y*1000, 3) });
                }
            }
            var bodies = A(((SW.IPartDoc)doc).GetBodies2(0, false));
            if (bodies.Length > 0)
            {
                var box = ((SW.IBody2)bodies[0]).GetBodyBox() as double[];
                if (box != null) d["box_mm"] = new[]{ R(box[0]*1000), R(box[1]*1000), R(box[2]*1000), R(box[3]*1000), R(box[4]*1000), R(box[5]*1000) };
            }
            var mp = doc.Extension.CreateMassProperty();
            if (mp != null) { try { mp.UseSystemUnits = true; } catch { } d["volume_mm3"] = Math.Round(N(() => mp.Volume) * 1e9, 2); }
            var cones = new List<object>();
            foreach (var b in bodies)
                foreach (var x in A(((SW.IBody2)b).GetFaces()))
                {
                    var fc = (SW.IFace2)x; var s = fc.GetSurface() as SW.ISurface;
                    if (s == null || !s.IsCone()) continue;
                    var q = (double[])s.ConeParams;
                    cones.Add(new Dictionary<string, object>{ {"R_mm", R(q[6]*1000)}, {"ha_deg", R(q[7]*180.0/Math.PI)},
                                                              {"area_mm2", Math.Round(Math.Abs(fc.GetArea())*1e6, 2)} });
                }
            d["cones"] = cones.ToArray();
        }
        catch (Exception ex) { d["error"] = ex.Message; }
        pts = all.ToArray();
        return d;
    }
}

static class Json
{
    public static string Write(object o) { var sb = new System.Text.StringBuilder(); W(sb, o); return sb.ToString(); }
    static void W(System.Text.StringBuilder sb, object o)
    {
        if (o == null) { sb.Append("null"); return; }
        if (o is string) { sb.Append('"').Append(((string)o).Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r")).Append('"'); return; }
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
