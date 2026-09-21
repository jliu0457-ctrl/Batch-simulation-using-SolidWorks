using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using SW = SolidWorks.Interop.sldworks;

public static class ProbeOpenCircularEdges
{
    static SW.ISldWorks Connect()
    {
        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch { com = Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34", true)); }
        var app = (SW.ISldWorks)com;
        app.Visible = true;
        return app;
    }

    static double[] TransformPoint(SW.IMathUtility mu, SW.IMathTransform transform, double x, double y, double z)
    {
        var point = (SW.IMathPoint)mu.CreatePoint(new[] { x, y, z });
        if (transform != null) point = (SW.IMathPoint)point.MultiplyTransform(transform);
        return ((double[])point.ArrayData).Take(3).ToArray();
    }

    static double[] TransformVector(SW.IMathUtility mu, SW.IMathTransform transform, double x, double y, double z)
    {
        var vector = (SW.IMathVector)mu.CreateVector(new[] { x, y, z });
        if (transform != null) vector = (SW.IMathVector)vector.MultiplyTransform(transform);
        return ((double[])vector.ArrayData).Take(3).ToArray();
    }

    public static string Run(string assemblyPath)
    {
        var app = Connect();
        int errors = 0, warnings = 0;
        var doc = (SW.IModelDoc2)app.OpenDoc6(assemblyPath, 2, 1, "开度45°", ref errors, ref warnings);
        if (doc == null) throw new Exception("OpenDoc6 failed: " + errors + "," + warnings);
        var assy = (SW.IAssemblyDoc)doc;
        var mu = (SW.IMathUtility)app.GetMathUtility();
        var rows = new List<object>();
        foreach (SW.IComponent2 comp in (object[])assy.GetComponents(false))
        {
            object bodyInfo;
            var bodiesObject = comp.GetBodies3(0, out bodyInfo);
            if (bodiesObject == null) continue;
            var transform = comp.GetTotalTransform(true);
            foreach (SW.IBody2 body in (object[])bodiesObject)
            {
                var edgesObject = body.GetEdges();
                if (edgesObject == null) continue;
                foreach (SW.IEdge edge in (object[])edgesObject)
                {
                    var curve = (SW.ICurve)edge.GetCurve();
                    if (curve == null || !curve.IsCircle()) continue;
                    var cp = (double[])curve.CircleParams;
                    if (cp == null || cp.Length < 7) continue;
                    var center = TransformPoint(mu, transform, cp[0], cp[1], cp[2]);
                    var normal = TransformVector(mu, transform, cp[3], cp[4], cp[5]);
                    var vertices = edge.GetStartVertex() == null && edge.GetEndVertex() == null ? 0 : 2;
                    rows.Add(new {
                        component = comp.Name2,
                        path = comp.GetPathName(),
                        center_m = center,
                        normal = normal,
                        radius_m = cp[6],
                        vertices = vertices,
                        length_m = curve.GetLength3(0.0, 2.0 * Math.PI)
                    });
                }
            }
        }
        return new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue }.Serialize(new {
            assembly = assemblyPath,
            open_errors = errors,
            open_warnings = warnings,
            circular_edges = rows
        });
    }

    public static int Main(string[] args)
    {
        try { Console.WriteLine(Run(args[0])); return 0; }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
