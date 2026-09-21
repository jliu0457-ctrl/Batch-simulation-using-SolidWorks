// A/B verification for the "D6@草图1 must follow c" fix (handoff doc 6.5.3).
//
// Opens the five core parts of a scratch V6 folder, writes c=31 into each (exactly the set the
// _diag_c_all run wrote), and in "fix" mode additionally writes D6@草图1 = c + 12 on the disc.
// Then it measures the disc's caliper (the real SevenVariableAdapter.MeasureCaliper, not a
// proxy), opens the assembly, force-rebuilds, and runs SolidWorks interference detection.
//
// Nothing is ever saved.  Run "base" first: if it reproduces 蝶板+大垫片 8763.73 and
// 蝶板+密封圈 2743.90 then the harness is faithful and "fix" means something.
//
// Usage: Verify-D6Fix.exe <folder> <assemblyFile> <output.json> <base|fix>
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class VerifyD6Fix
{
    static object[] A(object x) { return x as object[] ?? new object[0]; }
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }

    const double NewC = 31.0;
    const double D6Offset = 12.0;   // master: D6 44 = c 32 + 12
    const double XEndOffset = 66.0; // master: D1@草图2 34 = 66 - c 32

    static readonly string[] Tokens = { "03阀体", "08大垫片", "09密封圈", "10压板", "11蝶板" };

    // c target per part, from _analysis/c_all.json
    static Dictionary<string, string> CParam()
    {
        var m = new Dictionary<string, string>();
        m["03阀体"] = "D2@草图3";
        m["08大垫片"] = "D10@草图1";
        m["09密封圈"] = "D3@草图1";
        m["10压板"] = "D2@草图1";
        m["11蝶板"] = "D2@草图1";
        return m;
    }

    static SW.IModelDoc2 Open(SW.ISldWorks app, string path, int type, string config)
    {
        int e = 0, w = 0;
        var doc = (SW.IModelDoc2)app.OpenDoc6(path, type, 1, config, ref e, ref w);
        if (doc == null) throw new Exception("open failed (" + e + "," + w + "): " + Path.GetFileName(path));
        return doc;
    }

    // body bounding box in mm - a write that never reaches the kernel shows up here as an
    // unchanged box, which is the only way to tell a silent no-op from a real edit
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
            return new double[] { Math.Round(box[0] * 1000, 4), Math.Round(box[1] * 1000, 4), Math.Round(box[2] * 1000, 4),
                                  Math.Round(box[3] * 1000, 4), Math.Round(box[4] * 1000, 4), Math.Round(box[5] * 1000, 4) };
        }
        catch { return null; }
    }

    // The stem bore is the Ø45 shaft hole (and its Ø68 counterbore).  Both lie along Z, so their
    // axis point's x IS their axial position: this is what must not move.
    static List<object> Bores(SW.IModelDoc2 doc)
    {
        var rows = new List<object>();
        try
        {
            foreach (var body in A(((SW.IPartDoc)doc).GetBodies2(0, false)))
                foreach (var x in A(((SW.IBody2)body).GetFaces()))
                {
                    var f = (SW.IFace2)x; var s = f.GetSurface() as SW.ISurface;
                    if (s == null || !s.IsCylinder()) continue;
                    var q = (double[])s.CylinderParams;
                    double r = Math.Round(q[6] * 1000, 4);
                    if (Math.Abs(r - 22.5) > 0.1 && Math.Abs(r - 34) > 0.1) continue;
                    rows.Add(new { R_mm = r, axis = new[] { Math.Round(q[3], 4), Math.Round(q[4], 4), Math.Round(q[5], 4) },
                                   pt_mm = new[] { Math.Round(q[0] * 1000, 4), Math.Round(q[1] * 1000, 4), Math.Round(q[2] * 1000, 4) } });
                }
        }
        catch { }
        return rows;
    }

    public static int Main(string[] argv)
    {
        if (argv.Length < 4)
        {
            Console.Error.WriteLine("usage: Verify-D6Fix.exe <folder> <assemblyFile> <output.json> <base|fix>");
            return 2;
        }
        string folder = Path.GetFullPath(argv[0]);
        string asmPath = Path.GetFullPath(Path.Combine(folder, argv[1]));
        string outPath = argv[2];
        string modeArg = argv[3].Trim().ToLowerInvariant();
        bool fix2 = modeArg == "fix2";             // + D1@草图2, keeps the disc thickness
        bool fix = modeArg == "fix" || fix2;
        bool skipGasket = modeArg == "nogasket";   // control: reproduce _diag_c_nog
        bool noWrites = modeArg == "master";       // baseline: the template exactly as stored
        bool sBody = modeArg == "s-body", sRing = modeArg == "s-ring";
        bool sNoBody = modeArg == "s-nobody", sNoRing = modeArg == "s-noring";
        bool sPlate = modeArg == "s-plate", sNoPlate = modeArg == "s-noplate";
        bool sOnly = modeArg == "s" || sBody || sRing || sNoBody || sNoRing || sPlate || sNoPlate;
        // The solved s of trial v6_d6fix_001.  Override with a 5th argument (mm) to probe others.
        double sMm = argv.Length > 4 ? double.Parse(argv[4], System.Globalization.CultureInfo.InvariantCulture) : 191.48660175866145;

        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex) { Console.Error.WriteLine("cannot attach: " + ex.Message); return 3; }
        var app = (SW.ISldWorks)com;

        var r = new Dictionary<string, object>();
        r["mode"] = sOnly ? ("s-only [" + modeArg + "] s=" + sMm.ToString(System.Globalization.CultureInfo.InvariantCulture))
                  : (noWrites ? "master (no writes)"
                  : (fix2 ? "fix2 (c + D6 + D1@草图2)"
                  : (fix ? "fix (c + D6)"
                  : (skipGasket ? "nogasket (c, no gasket)" : "base (c only)"))));
        r["folder"] = folder;
        var log = new List<object>();
        var writes = new List<object>();
        r["writes"] = writes;
        var parts = new Dictionary<string, SW.IModelDoc2>();

        try
        {
            app.CloseAllDocuments(true);

            foreach (var tok in Tokens)
            {
                string f = Directory.GetFiles(folder, "*.SLDPRT").FirstOrDefault(x => x.Contains(tok));
                if (f == null) throw new Exception("part not found: " + tok);
                parts[tok] = Open(app, f, 1, "");
            }
            var disc = parts["11蝶板"];

            var boxesBefore = new Dictionary<string, double[]>();
            foreach (var kv in parts) { kv.Value.ForceRebuild3(false); boxesBefore[kv.Key] = Box(kv.Value); }

            disc.ForceRebuild3(false);
            var cal0 = SevenVariableAdapter.MeasureCaliper(disc);
            r["caliper_baseline"] = new { lower_mm = cal0.lower_mm, upper_mm = cal0.upper_mm, band_mm = cal0.band_mm };

            var cp = CParam();
            foreach (var tok in Tokens)
            {
                if (noWrites || sOnly) break;
                if (skipGasket && tok == "08大垫片") continue;
                var d = parts[tok].Parameter(cp[tok]) as SW.IDimension;
                if (d == null) { writes.Add(new { token = tok, parameter = cp[tok], found = false }); continue; }
                double before = d.SystemValue;
                d.SystemValue = NewC / 1000.0;
                writes.Add(new { token = tok, parameter = cp[tok], found = true,
                                 before_mm = Math.Round(before * 1000, 4), after_mm = Math.Round(d.SystemValue * 1000, 4) });
            }
            if (fix)
            {
                var d6 = disc.Parameter("D6@草图1") as SW.IDimension;
                if (d6 == null) writes.Add(new { token = "11蝶板", parameter = "D6@草图1", found = false });
                else
                {
                    double before = d6.SystemValue;
                    d6.SystemValue = (NewC + D6Offset) / 1000.0;
                    writes.Add(new { token = "11蝶板", parameter = "D6@草图1", found = true,
                                     before_mm = Math.Round(before * 1000, 4), after_mm = Math.Round(d6.SystemValue * 1000, 4) });
                }
            }
            if (fix2)
            {
                // The disc's +x extreme is x = +D1@草图2 (measured: a +2 mm step moves xmax by
                // exactly +2).  Both ends must travel together or the disc is stretched, so this
                // one runs the other way: D1@草图2 = 66 - c  (master 34 = 66 - 32).
                var d2 = disc.Parameter("D1@草图2") as SW.IDimension;
                if (d2 == null) writes.Add(new { token = "11蝶板", parameter = "D1@草图2", found = false });
                else
                {
                    double before = d2.SystemValue;
                    d2.SystemValue = (XEndOffset - NewC) / 1000.0;
                    writes.Add(new { token = "11蝶板", parameter = "D1@草图2", found = true,
                                     before_mm = Math.Round(before * 1000, 4), after_mm = Math.Round(d2.SystemValue * 1000, 4) });
                }
            }
            if (sOnly)
            {
                // Mirrors SevenVariableAdapter.SetConstruction's V6 branch: the five parts that
                // carry s itself, plus the three cone dimensions derived from s/2 - 1.575.
                double radial = sMm / 2 - 1.575;
                var group = new object[][] {
                    new object[]{"03阀体",  "D5@草图5", sMm},
                    new object[]{"08大垫片", "D6@草图1", sMm},
                    new object[]{"09密封圈", "D7@草图1", sMm},
                    new object[]{"10压板",  "D5@草图1", sMm},
                    new object[]{"11蝶板",  "D3@草图3", sMm},
                    new object[]{"11蝶板",  "D5@草图3", radial},
                    new object[]{"08大垫片", "D8@草图1", 2 * radial},
                    // 2026-09-20: 10压板 D8@草图1 is DELIBERATELY absent - it is now the constant
                    // 1.575 (the anchor-to-construction-end distance) and writing ~95.6 into it
                    // breaks the sketch.  The plate inherits s through the construction line.
                    new object[]{"09密封圈", "D10@草图1", sMm / 2 + 12.0 - 70.0},
                    new object[]{"10压板",  "D13@草图1", sMm / 2 + 13.0 - 47.5},
                };
                foreach (var row in group)
                {
                    string tok = (string)row[0], nm = (string)row[1];
                    double v = (double)row[2];
                    // Part filter for the "who responds wrongly" probe: s-body / s-ring write only
                    // that one part; s-nobody / s-noring leave that one part at the master value.
                    if (sBody && tok != "03阀体") continue;
                    if (sRing && tok != "09密封圈") continue;
                    if (sNoBody && tok == "03阀体") continue;
                    if (sNoRing && tok == "09密封圈") continue;
                    if (sPlate && tok != "10压板") continue;
                    if (sNoPlate && tok == "10压板") continue;
                    var dim = parts[tok].Parameter(nm) as SW.IDimension;
                    if (dim == null) { writes.Add(new { token = tok, parameter = nm, found = false }); continue; }
                    double before = dim.SystemValue;
                    dim.SystemValue = v / 1000.0;
                    writes.Add(new { token = tok, parameter = nm, found = true,
                                     before_mm = Math.Round(before * 1000, 4), after_mm = Math.Round(dim.SystemValue * 1000, 4) });
                }
            }

            // Rebuild FIRST, then read back - reading the box before the rebuild returns the
            // stale pre-write box and makes every change look like a no-op.
            foreach (var kv in parts) kv.Value.ForceRebuild3(false);

            var readback = new List<object>();
            foreach (var tok in Tokens)
            {
                if (skipGasket && tok == "08大垫片") continue;
                var nm = cp[tok];
                var d = parts[tok].Parameter(nm) as SW.IDimension;
                readback.Add(new { token = tok, parameter = nm,
                                   readback_mm = d == null ? (double?)null : Math.Round(d.SystemValue * 1000, 4),
                                   box_before_mm = boxesBefore[tok], box_after_mm = Box(parts[tok]) });
            }
            if (fix)
            {
                var d = disc.Parameter("D6@草图1") as SW.IDimension;
                readback.Add(new { token = "11蝶板", parameter = "D6@草图1",
                                   readback_mm = d == null ? (double?)null : Math.Round(d.SystemValue * 1000, 4),
                                   box_before_mm = boxesBefore["11蝶板"], box_after_mm = Box(disc) });
            }
            if (fix2)
            {
                var d = disc.Parameter("D1@草图2") as SW.IDimension;
                readback.Add(new { token = "11蝶板", parameter = "D1@草图2",
                                   readback_mm = d == null ? (double?)null : Math.Round(d.SystemValue * 1000, 4),
                                   box_before_mm = boxesBefore["11蝶板"], box_after_mm = Box(disc) });
            }
            r["readback"] = readback;
            r["disc_box_before_mm"] = boxesBefore["11蝶板"];
            r["disc_box_after_mm"] = Box(disc);
            r["disc_bore_mm"] = Bores(disc);   // the stem bore must stay on the shaft axis

            // Save the scratch copy so the assembly is guaranteed to see every edit.  V6 parts
            // carry no equations, so Save3 is silent; the real template is never touched.
            var saved = new List<string>();
            foreach (var kv in parts)
            {
                int se = 0, sw = 0;
                try { kv.Value.Save3(1, ref se, ref sw); saved.Add(kv.Key + ":" + se); }
                catch (Exception ex) { saved.Add(kv.Key + ":EX " + ex.Message); }
            }
            r["saved"] = saved.ToArray();

            disc.ForceRebuild3(false);
            var cal1 = SevenVariableAdapter.MeasureCaliper(disc);
            r["caliper_after_write"] = new { lower_mm = cal1.lower_mm, upper_mm = cal1.upper_mm, band_mm = cal1.band_mm };
            r["caliper_delta_mm"] = cal1.midpoint_mm - cal0.midpoint_mm;

            var assy = Open(app, asmPath, 2, "\u5f00\u5ea645\u00b0");
            r["assembly"] = S(() => assy.GetPathName());
            r["assembly_rebuild_ok"] = assy.ForceRebuild3(false);

            var adoc = (SW.IAssemblyDoc)assy;
            var comps = new List<string>();
            foreach (var o in A(adoc.GetComponents(false))) { var c = o as SW.IComponent2; if (c != null) comps.Add(S(() => c.Name2)); }
            r["components"] = comps.ToArray();

            var mgr = (SW.IInterferenceDetectionMgr)adoc.InterferenceDetectionManager;
            if (mgr == null) throw new Exception("InterferenceDetectionManager unavailable");
            mgr.TreatCoincidenceAsInterference = false;
            mgr.IncludeMultibodyPartInterferences = false;
            object raw = mgr.GetInterferences();
            var arr = raw as Array;
            int n = arr == null ? 0 : arr.Length;
            r["interference_count"] = n;
            var rows = new List<object>();
            for (int i = 0; i < n; i++)
            {
                SW.IInterference it = null;
                try { it = arr.GetValue(i) as SW.IInterference; } catch (Exception ex) { rows.Add(new { index = i, error = ex.Message }); continue; }
                if (it == null) continue;
                var names = new List<string>();
                try
                {
                    var cs = it.Components;
                    if (cs is Array) foreach (object o in (Array)cs) { var c = o as SW.IComponent2; names.Add(c == null ? "<null>" : S(() => c.Name2)); }
                }
                catch (Exception ex) { names.Add("<err " + ex.Message + ">"); }
                double vol = 0; try { vol = it.Volume * 1e9; } catch { }
                // The overlap body sits in assembly coordinates: its box says WHERE the parts met.
                object box = null;
                try
                {
                    var ib = it.GetInterferenceBody() as SW.IBody2;
                    var bb = ib == null ? null : ib.GetBodyBox() as double[];
                    if (bb != null && bb.Length == 6)
                        box = new[]{ Math.Round(bb[0]*1000,2), Math.Round(bb[1]*1000,2), Math.Round(bb[2]*1000,2),
                                     Math.Round(bb[3]*1000,2), Math.Round(bb[4]*1000,2), Math.Round(bb[5]*1000,2) };
                }
                catch { }
                rows.Add(new { index = i, components = names.ToArray(), volume_mm3 = vol, box_mm = box });
            }
            r["interferences"] = rows.ToArray();

            // Second pass with coincident contact COUNTED.  "No interference" while touching
            // faces are ignored can also mean "these two parts now sit exactly flush", which is
            // not the same thing as a correct fit.  Only the aggregate is reported so the list
            // stays readable - with coincidence on, every designed mating face shows up.
            try
            {
                mgr.TreatCoincidenceAsInterference = true;
                object raw2 = mgr.GetInterferences();
                var arr2 = raw2 as Array;
                int n2 = arr2 == null ? 0 : arr2.Length;
                double tot2 = 0;
                for (int i = 0; i < n2; i++)
                {
                    var it2 = arr2.GetValue(i) as SW.IInterference;
                    if (it2 != null) { try { tot2 += it2.Volume * 1e9; } catch { } }
                }
                r["coincident_count"] = n2;
                r["coincident_total_mm3"] = Math.Round(tot2, 3);
            }
            catch (Exception ex) { r["coincident_error"] = ex.Message; }
            try { mgr.TreatCoincidenceAsInterference = false; } catch { }

            try { mgr.Done(); } catch { }
            r["finished"] = true;
        }
        catch (Exception ex) { r["exception"] = ex.ToString(); }
        finally
        {
            r["log"] = log.ToArray();
            try { app.CloseAllDocuments(true); } catch { }   // never saved
        }

        File.WriteAllText(outPath, Json.Write(r), new System.Text.UTF8Encoding(false));
        Console.WriteLine("written: " + outPath);
        return 0;
    }

    static class Json
    {
        public static string Write(object o) { var sb = new System.Text.StringBuilder(); W(sb, o); return sb.ToString(); }
        static void W(System.Text.StringBuilder sb, object o)
        {
            if (o == null) { sb.Append("null"); return; }
            var s = o as string;
            if (s != null) { sb.Append('"').Append(s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r").Replace("\t", "\\t")).Append('"'); return; }
            if (o is bool) { sb.Append(((bool)o) ? "true" : "false"); return; }
            if (o is double) { sb.Append(((double)o).ToString("R", System.Globalization.CultureInfo.InvariantCulture)); return; }
            if (o is int) { sb.Append(((int)o).ToString(System.Globalization.CultureInfo.InvariantCulture)); return; }
            var d = o as System.Collections.IDictionary;
            if (d != null)
            {
                sb.Append('{'); bool first = true;
                foreach (System.Collections.DictionaryEntry e in d)
                { if (!first) sb.Append(','); first = false; W(sb, e.Key); sb.Append(':'); W(sb, e.Value); }
                sb.Append('}'); return;
            }
            var en = o as System.Collections.IEnumerable;
            if (en != null)
            {
                sb.Append('['); bool first = true;
                foreach (var e in en) { if (!first) sb.Append(','); first = false; W(sb, e); }
                sb.Append(']'); return;
            }
            // plain anonymous types: reflect over the public properties
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
}
