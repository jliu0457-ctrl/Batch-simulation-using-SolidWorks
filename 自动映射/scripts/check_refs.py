#!/usr/bin/env python3
"""Print, for each assembly, the absolute path of every core part it references.

Why: the scratch folders were made with cp -r.  If an assembly stored ABSOLUTE references
back into assembly_batch_v7 then opening a copy would silently load the ORIGINAL parts, and
any measurement taken on the copy would be meaningless.  Run this before trusting a copy.
"""
import glob
import os
import sys
import time

import pythoncom
import win32com.client as win32

TARGETS = ["大垫片", "密封圈", "压板", "蝶板", "阀体", "阀轴"]
FOLDERS = [
    ("母版", r"working\assembly_batch_v7"),
    ("_c_only_test", r"working\_c_only_test"),
    ("_nounion4", r"working\_nounion4"),
    ("_nogasketmates", r"working\_nogasketmates"),
]


def attach(retries=15, delay=3.0):
    last = None
    for _ in range(retries):
        try:
            return win32.GetActiveObject("SldWorks.Application")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"cannot attach: {last}")


def main():
    pythoncom.CoInitialize()
    sw = attach()
    for _ in range(20):
        d = sw.ActiveDoc
        if d is None:
            break
        sw.CloseDoc(d.GetTitle)

    for label, folder in FOLDERS:
        found = glob.glob(folder + r"\*.SLDASM")
        if not found:
            print(f"{label}: 没有装配体")
            continue
        result = sw.OpenDoc6(os.path.abspath(found[0]), 2, 1, "开度45°", 0, 0)
        doc = result[0] if isinstance(result, tuple) else result
        if doc is None:
            print(f"{label}: 打不开")
            continue
        late = win32.dynamic.Dispatch(doc._oleobj_)
        print(f"=== {label} ({found[0]}) ===")
        for comp in late.GetComponents(False):
            name = str(getattr(comp, "Name2", "") or "")
            if not any(t in name for t in TARGETS):
                continue
            path = getattr(comp, "GetPathName", "")
            if callable(path):
                path = path()
            path = str(path or "")
            exists = os.path.exists(path) if path else False
            print(f"   {name[:32]:<34} 存在={exists}  {path}")
        sw.CloseDoc(doc.GetTitle)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
