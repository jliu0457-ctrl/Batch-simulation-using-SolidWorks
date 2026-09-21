using System;
using System.Linq;
using System.Reflection;
public static class DumpApi
{
    public static int Main(string[] a)
    {
        var asm = Assembly.LoadFrom(a[0]);
        foreach (var tn in a.Skip(1))
        {
            var t = asm.GetType(tn, false);
            if (t == null) { Console.WriteLine(tn + ": NOT FOUND"); continue; }
            Console.WriteLine("== " + tn + " ==");
            foreach (var m in t.GetMembers(BindingFlags.Public | BindingFlags.Instance)
                               .Select(x => x.Name).Distinct().OrderBy(x => x))
                if (a.Length <= 2 || m.ToLower().Contains(a[a.Length-1].ToLower())) Console.WriteLine("   " + m);
        }
        return 0;
    }
}
