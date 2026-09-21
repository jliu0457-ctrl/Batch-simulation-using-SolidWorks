using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using SolidWorks.Interop.sldworks;

public static class Inspect012Mates
{
    private static IFeature Find(IModelDoc2 doc, string name)
    {
        for (IFeature f = doc.FirstFeature() as IFeature; f != null; f = f.GetNextFeature() as IFeature)
        {
            if (String.Equals(f.Name, name, StringComparison.Ordinal)) return f;
            for (IFeature s = f.GetFirstSubFeature() as IFeature; s != null; s = s.GetNextSubFeature() as IFeature)
                if (String.Equals(s.Name, name, StringComparison.Ordinal)) return s;
        }
        return null;
    }

    private static void PrintMate(IModelDoc2 doc, string name)
    {
        IFeature f = Find(doc, name);
        if (f == null) { Console.WriteLine("MATE|" + name + "|missing"); return; }
        IMate2 mate = f.GetSpecificFeature2() as IMate2;
        Console.WriteLine("MATE|" + name + "|feature_type=" + f.GetTypeName2() + "|error=" + f.GetErrorCode() + "|mate_type=" + (mate == null ? -1 : mate.Type));
        if (mate == null) return;
        for (int i = 0; i < mate.GetMateEntityCount(); i++)
        {
            IMateEntity2 entity = mate.MateEntity(i);
            IComponent2 component = entity.ReferenceComponent;
            double[] p = entity.EntityParams as double[];
            Console.WriteLine("MATE_ENTITY|" + name + "|" + i + "|component=" + (component == null ? "<assembly>" : component.Name2) + "|reference_type=" + entity.ReferenceType2 + "|parameters=" + (p == null ? "" : String.Join(",", p.Select(x => x.ToString("R")))));
        }
    }

    private static void PrintDesignTable(IComponent2 component)
    {
        IModelDoc2 part = component.GetModelDoc2() as IModelDoc2;
        if (part == null) { Console.WriteLine("PART|" + component.Name2 + "|unresolved"); return; }
        IDesignTable table = part.GetDesignTable() as IDesignTable;
        if (table == null) { Console.WriteLine("PART|" + component.Name2 + "|design_table=none"); return; }
        bool attached = table.Attach();
        Console.WriteLine("PART|" + component.Name2 + "|design_table_attach=" + attached + "|columns=" + table.GetColumnCount());
        if (attached)
            for (int c = 0; c < table.GetColumnCount(); c++) Console.WriteLine("DESIGN_TABLE_HEADER|" + component.Name2 + "|" + c + "|" + table.GetHeaderText(c));
    }

    public static int Main(string[] args)
    {
        if (args.Length < 1 || args.Length > 2) { Console.Error.WriteLine("Expected assembly path and optional output path."); return 2; }
        string path = Path.GetFullPath(args[0]);
        if (args.Length == 2)
        {
            string output = Path.GetFullPath(args[1]);
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            Console.SetOut(new StreamWriter(output, false) { AutoFlush = true });
            Console.SetError(Console.Out);
        }
        try
        {
            ISldWorks app = (ISldWorks)Marshal.GetActiveObject("SldWorks.Application");
            Console.WriteLine("CONNECTED|revision=" + app.RevisionNumber() + "|visible=" + app.Visible);
            int errors = 0, warnings = 0;
            IModelDoc2 doc = app.OpenDoc6(path, 2, 3, "开度45°", ref errors, ref warnings) as IModelDoc2;
            if (doc == null) throw new InvalidOperationException("OpenDoc6 returned null: errors=" + errors + ", warnings=" + warnings);
            Console.WriteLine("OPENED|path=" + doc.GetPathName() + "|errors=" + errors + "|warnings=" + warnings + "|configuration=" + doc.ConfigurationManager.ActiveConfiguration.Name);
            foreach (string name in new[] { "重合3", "重合4", "锁定1", "锁定2", "锁定3", "同心3" }) PrintMate(doc, name);
            IAssemblyDoc assembly = doc as IAssemblyDoc;
            foreach (object o in (assembly.GetComponents(false) as object[] ?? new object[0]))
            {
                IComponent2 component = o as IComponent2;
                if (component != null && new[] { "08大垫片", "09密封圈", "10压板", "11蝶板" }.Any(t => component.Name2.Contains(t))) PrintDesignTable(component);
            }
            app.CloseDoc(doc.GetTitle());
            Console.WriteLine("DONE|closed_read_only_document=true");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("ERROR|" + ex);
            return 1;
        }
    }
}
