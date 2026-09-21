// Changes ONE dimension on a scratch copy of a part and reports what moved.
//
// This is the control experiment for the 194.37 construction: 03阀体 is the part where the
// dimension demonstrably works, so its response is the reference every other part has to match.
// Understanding its response is what tells us what is missing on 09密封圈.
//
// Prediction being tested (body): the 194.37 construction line runs from (-32, -92.9393) at
// 8.25 deg from vertical - it is perpendicular to the phi line.  So raising 194.37 to 205 should
// slide its far end to (-32, -92.9393) + 205*(0.14349, 0.98965) = (-2.584, 109.939), and the
// cut line, whose end coincides with it, should translate by (+1.525, +10.52) - of which the
// component perpendicular to the cut line is +10.12, the part that actually moves the cone.
//
// NEVER saves.  Run it on a copy, never on the master or the desktop original.
//
// Usage: Probe-DimensionResponse.exe <copy.SLDPRT> <dimValueMm> <newValueMm> <out.json>
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class DimensionResponse
{
    static object[] A(object x) { return x as object[] ?? new object[0]; }
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }
    static double N(Func<double> f) { try { return f(); } catch { return 0; } }
    static object R(double v) { return Math.Round(v, 3); }

    public static int Main(string[] argv)
    {
        if (argv.Length < 4) { Console.Error.WriteLine("usage: Probe-DimensionResponse.exe <copy.SLDPRT> <findMm> <setMm> <out.json>"); return 2; }
        string part = argv[0], outPath = argv[3];
        double findMm = double.Parse(argv[1], System.Globalization.CultureInfo.InvariantCulture);
        double setMm  = double.Parse(argv[2], System.Globalization.CultureInfo.InvariantCulture);
        double find2 = argv.Length > 5 ? double.Parse(argv[4], System.Globalization.CultureInfo.InvariantCulture) : double.NaN;
        double set2  = argv.Length > 6 ? double.Parse(argv[5], System.Globalization.CultureInfo.InvariantCulture) : double.NaN;

        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex) { Console.Error.WriteLine("cannot attach: " + ex.Message); return 3; }
        var app = (SW.ISldWorks)com;

        // Refuse if a same-named document is already open: OpenDoc6 would silently reuse it
        // (error 65536) and we would measure the wrong part.
        string target = Path.GetFileNameWithoutExtension(part);
        foreach (var o in A(app.GetDocuments()))
        {
            var d = o as SW.IModelDoc2; if (d == null) continue;
            if (string.Equals(S(() => Path.GetFileNameWithoutExtension(d.GetPathName())), target, StringComparison.OrdinalIgnoreCase))
            { Console.Error.WriteLine("refusing: '" + target + "' is already open"); return 5; }
        }

        int err = 0, warn = 0;
        var doc = (SW.IModelDoc2)app.OpenDoc6(part, 1, 0, "", ref err, ref warn);
        if (doc == null) { Console.Error.WriteLine("OpenDoc6 failed err=" + err); return 7; }

        var r = new Dictionary<string, object>();
        r["part"] = part; r["find_mm"] = findMm; r["set_mm"] = setMm;
        try
        {
            doc.ForceRebuild3(false);
            r["before"] = Snapshot(doc, out var ptsB);
            r["points_before"] = ptsB;

            // Find the dimension by VALUE, not by name: the feature tree is localised, so
            // "D5@草图5" cannot be written literally, and the value is unique here.
            SW.IDimension hit = null; string hitName = null; string hitSketch = null;
            for (var f = (SW.IFeature)doc.FirstFeature(); f != null; f = (SW.IFeature)f.GetNextFeature())
            {
                if (f.GetSpecificFeature2() as SW.ISketch == null) continue;
                object disp = null;
                try { disp = f.GetFirstDisplayDimension(); } catch { }
                int guard = 0;
                while (disp != null && guard++ < 200)
                {
                    try
                    {
                        var dd = disp as SW.IDisplayDimension;
                        var d = dd == null ? null : dd.GetDimension2(0) as SW.IDimension;
                        if (d != null && Math.Abs(d.SystemValue * 1000.0 - findMm) < 0.005)
                        { hit = d; hitName = S(() => d.FullName); hitSketch = f.Name; }
                    }
                    catch { }
                    try { disp = f.GetNextDisplayDimension(disp); } catch { disp = null; }
                }
                if (hit != null) break;
            }
            r["dim_found"] = hitName; r["dim_sketch"] = hitSketch;
            if (hit == null) { r["error"] = "no dimension equal to " + findMm + " mm"; }
            else
            {
                // Use SetSystemValue3(value, 1, null) - the SAME call SevenVariableAdapter.Set()
                // makes.  Assigning .SystemValue instead is NOT equivalent: it left the body
                // looking unresponsive in the first version of this probe, while the user
                // watching the UI saw the sketch widen.  The write method decides the answer.
                // optional second dimension - the adapter writes several at once and the parts
                // respond to the COMBINATION, not to any single one in isolation
                if (!double.IsNaN(find2))
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
                                if (d2 != null && Math.Abs(d2.SystemValue * 1000.0 - find2) < 0.005)
                                { r["dim2_found"] = S(() => d2.FullName); r["dim2_status"] = d2.SetSystemValue3(set2 / 1000.0, 1, null); dsp = null; break; }
                            }
                            catch { }
                            try { dsp = f2.GetNextDisplayDimension(dsp); } catch { dsp = null; }
                        }
                        if (r.ContainsKey("dim2_found")) break;
                    }
                }
                try { r["set_status"] = hit.SetSystemValue3(setMm / 1000.0, 1, null); r["set_ok"] = true; }
                catch (Exception ex) { r["set_ok"] = false; r["set_error"] = ex.Message; }
                r["driven_after_write"] = S(() => ((SW.IDimension)hit).DrivenState.ToString());
                // Read it straight back.  A DRIVEN (reference) dimension accepts the assignment
                // without raising, then keeps its old value - which looks exactly like "nothing
                // depends on this dimension" unless the value is checked.
                r["readback_mm"] = Math.Round(N(() => hit.SystemValue) * 1000, 4);
                r["driven"] = S(() => ((SW.IDimension)hit).DrivenState.ToString());
                doc.EditRebuild3();
                doc.ForceRebuild3(false);
                r["readback_after_rebuild_mm"] = Math.Round(N(() => hit.SystemValue) * 1000, 4);
                r["after"] = Snapshot(doc, out var ptsA);
                r["points_after"] = ptsA;

                // per-point displacement: the whole point of the run
                var moves = new List<object>();
                if (ptsB != null && ptsA != null)
                    for (int i = 0; i < ptsB.Length && i < ptsA.Length; i++)
                    {
                        var p0 = ptsB[i] as double[]; var p1 = ptsA[i] as double[];
                        if (p0 == null || p1 == null) continue;
                        double dx = p1[0]-p0[0], dy = p1[1]-p0[1];
                        if (Math.Abs(dx) < 1e-4 && Math.Abs(dy) < 1e-4) continue;
                        moves.Add(new Dictionary<string, object>{
                            {"from", new[]{ R(p0[0]), R(p0[1]) }},
                            {"to",   new[]{ R(p1[0]), R(p1[1]) }},
                            {"d",    new[]{ R(dx), R(dy) }} });
                    }
                r["moved_points"] = moves.ToArray();
            }
        }
        catch (Exception ex) { r["exception"] = ex.ToString(); }

        // close WITHOUT saving - this is a probe, not an edit
        try { doc.ClearSelection2(true); doc.SetSaveFlag(); app.CloseDoc(doc.GetTitle()); } catch { }
        File.WriteAllText(outPath, Json.Write(r), new System.Text.UTF8Encoding(false));
        Console.WriteLine("written: " + outPath);
        return 0;
    }

    // every sketch point of every sketch, plus the solid's outline and cone list
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
            var pd = doc as SW.IPartDoc;
            var bodies = A(pd.GetBodies2(0, false));
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
                    cones.Add(new Dictionary<string, object>{
                        {"R_mm", R(q[6]*1000)},
                        {"ha_deg", R(q[7]*180.0/Math.PI)},
                        {"area_mm2", Math.Round(Math.Abs(fc.GetArea())*1e6, 2)}});
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
