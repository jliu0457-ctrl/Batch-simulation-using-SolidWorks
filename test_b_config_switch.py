#!/usr/bin/env python3
"""测试 B：只做「切配置 + 重建 + 保存」，不加同心配合。

目的：把 RepairAssembly.cs 里的四个动作分成两组——
      (配置切换 + ForceRebuild3 + Save3)  vs  (AddMate5 同心配合)
判断哪一组才是让内部流体域失封的原因。

做完后由 Run-FlowSingle.py --validate-flow-rebuild 检查封闭性。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pythoncom
import win32com.client as win32

ROOT = Path(r"C:\Users\liujunxiang\Desktop\流体仿真2")
SRC = ROOT / "自动映射" / "working" / "assembly_repair_v5"
DST = ROOT / "自动映射" / "working" / "seven_variable_trials" / "template_cfgtest_012"
OUT = ROOT / "test_b_result.json"


def find_pid():
    out = subprocess.run(
        ["tasklist", "/fi", "IMAGENAME eq SLDWORKS.exe", "/fo", "csv", "/nh"],
        capture_output=True, text=True, errors="replace",
    ).stdout
    for line in out.splitlines():
        if line.lower().startswith('"sldworks.exe"'):
            return int(line.split(",")[1].strip('"'))
    raise SystemExit("no SLDWORKS.exe running")


def attr(obj, *names):
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if callable(value):
            try:
                return value()
            except Exception:
                continue
        return value
    return None


def main():
    r = {}
    pythoncom.CoInitialize()

    sw = win32.GetActiveObject("SldWorks.Application")
    r["gate"] = {"revision": str(sw.RevisionNumber()), "visible": bool(sw.Visible)}

    if not DST.exists():
        DST.mkdir(parents=True)
        for f in sorted(SRC.iterdir()):
            if f.suffix.lower() in (".sldasm", ".sldprt"):
                shutil.copy2(f, DST / f.name)
    assembly = next(DST.glob("*.SLDASM"))
    r["assembly"] = str(assembly)

    sys.path.insert(0, str(ROOT))
    import flow_transfer as ft

    api = ft.connect()
    app = api.Attach2RunningObject2(find_pid())
    model = app.OpenDocument(str(assembly), "开度45°")
    if model is None:
        raise SystemExit("OpenDocument failed")

    doc = sw.ActiveDoc
    r["opened"] = str(attr(doc, "GetTitle"))

    # 1) 配置清单
    try:
        names = doc.GetConfigurationNames()
        r["configurations"] = [str(x) for x in names]
    except Exception as exc:
        r["configurations_error"] = str(exc)[:200]

    # 2) 切到「默认」并重建（复刻 RepairAssembly.cs 的第一步）
    steps = []
    for conf in ("默认", "开度45°"):
        try:
            ok = bool(doc.ShowConfiguration2(conf))
        except Exception as exc:
            ok = "error: %s" % exc
        try:
            rb = bool(doc.ForceRebuild3(False))
        except Exception as exc:
            rb = "error: %s" % exc
        steps.append({"config": conf, "show_ok": ok, "rebuild_ok": rb})
    r["steps"] = steps

    # 3) 保存（这一步是 write）
    try:
        r["save_ok"] = bool(doc.Save())
    except Exception as exc:
        r["save_error"] = str(exc)[:200]

    # 4) 关掉，让 Run-FlowSingle 重新打开
    try:
        sw.CloseDoc(str(attr(doc, "GetTitle")))
        r["closed"] = True
    except Exception as exc:
        r["closed"] = "error: %s" % exc

    OUT.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written:", OUT)
    for k in ("configurations", "steps", "save_ok", "closed"):
        print(k, "=", r.get(k))


if __name__ == "__main__":
    main()
