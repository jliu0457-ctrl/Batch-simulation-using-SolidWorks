#!/usr/bin/env python3
"""Read-only: dump the disc part's equation table and confirm which pywin32 access
pattern works for IEquationMgr.  Attaches to the running SolidWorks; opens nothing."""
import json
import sys
from pathlib import Path

import pythoncom
import win32com.client as win32

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import flow_transfer as ft

PART = r"C:\Users\liujunxiang\Desktop\流体仿真2\自动映射\working\assembly_batch_v7\8“D94R3Y-CL600C-11蝶板.SLDPRT"
OUT = r"C:\Users\liujunxiang\Desktop\流体仿真2\自动映射\_analysis\disc_eq_probe.json"


def main():
    pythoncom.CoInitialize()
    sw = win32.GetActiveObject("SldWorks.Application")
    print("attached, revision =", sw.RevisionNumber())

    doc = sw.ActiveDoc
    if doc is None:
        print("no active document; opening the disc")
        # OpenDoc6(Filename, Type=1 part, Options=1 silent, Configuration, Errors, Warnings)
        api = ft.connect()
        interactive = ft.safe_get(api, "Attach2RunningObject")
        model = interactive.OpenDocument(PART, "")
        print("OpenDocument ->", model is not None)
        doc = sw.ActiveDoc
    if doc is None:
        print("FAILED to get a document")
        return 1

    print("document:", doc.GetTitle)

    em = doc.GetEquationMgr()
    count = em.GetCount
    print("equation count:", count)

    rows = []
    for i in range(count):
        eq = None
        val = None
        for getter in ("Equation", "get_Equation"):
            try:
                eq = getattr(em, getter)(i)
                used = getter
                break
            except Exception as exc:  # noqa: BLE001
                used = f"{getter} failed: {exc}"
        for getter in ("Value", "get_Value"):
            try:
                val = getattr(em, getter)(i)
                break
            except Exception:  # noqa: BLE001
                pass
        rows.append({"i": i, "equation": eq, "value": val, "via": used})
        print(f"  {i:3d}  {eq!s:<58} = {val}")

    with open(OUT, "w", encoding="utf-8") as stream:
        json.dump({"title": doc.GetTitle, "path": doc.GetPathName, "count": count, "equations": rows},
                  stream, ensure_ascii=False, indent=1)
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
