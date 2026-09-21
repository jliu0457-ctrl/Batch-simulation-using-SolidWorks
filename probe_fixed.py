#!/usr/bin/env python3
"""只读探针：打开一份原样模板副本，记录每个组件的 IsFixed / 抑制状态 / 变换矩阵。

不修改任何东西、不保存。用途：验证「加同心配合时 SolidWorks 会不会移动阀体而不是阀轴」。
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
DST = ROOT / "自动映射" / "working" / "seven_variable_trials" / "template_probe_011"
OUT = ROOT / "probe_fixed_result.json"


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
    report = {}
    pythoncom.CoInitialize()

    sw = win32.GetActiveObject("SldWorks.Application")
    report["gate"] = {
        "revision": str(sw.RevisionNumber()),
        "visible": bool(sw.Visible),
        "active_doc": str(attr(sw.ActiveDoc, "GetTitle")) if sw.ActiveDoc else None,
    }

    if not DST.exists():
        DST.mkdir(parents=True)
        for f in sorted(SRC.iterdir()):
            if f.suffix.lower() in (".sldasm", ".sldprt"):
                shutil.copy2(f, DST / f.name)
    copied = len([f for f in DST.iterdir() if f.suffix.lower() in (".sldasm", ".sldprt")])
    report["copied_cad_files"] = copied
    assembly = next(DST.glob("*.SLDASM"))
    report["assembly"] = str(assembly)

    sys.path.insert(0, str(ROOT))
    import flow_transfer as ft

    api = ft.connect()
    app = api.Attach2RunningObject2(find_pid())
    if app is None:
        raise SystemExit("attach failed")
    model = app.OpenDocument(str(assembly), "开度45°")
    if model is None:
        raise SystemExit("OpenDocument failed")

    doc = sw.ActiveDoc
    report["opened_doc"] = str(attr(doc, "GetTitle"))
    try:
        cm = attr(doc, "ConfigurationManager")
        report["active_config"] = str(attr(attr(cm, "ActiveConfiguration"), "Name")) if cm else None
    except Exception as exc:
        report["active_config"] = "unavailable: %s" % exc

    # 组件枚举：先试原生，再试 makepy 早绑定
    comps = None
    for how in ("direct", "gendispatch"):
        try:
            target = doc if how == "direct" else win32.gencache.EnsureDispatch(doc)
            comps = target.GetComponents(False)
            report["component_enum_via"] = how
            break
        except Exception as exc:
            report["component_enum_error_" + how] = str(exc)[:200]
    if comps is None:
        raise SystemExit("cannot enumerate components")

    rows = []
    for c in comps:
        row = {
            "name": str(attr(c, "Name2", "Name")),
            "is_fixed": bool(attr(c, "IsFixed")),
            "suppression": attr(c, "GetSuppression"),
            "is_virtual": attr(c, "IsVirtual"),
            "referenced_configuration": attr(c, "ReferencedConfiguration"),
            "is_lightweight": attr(c, "IsLightWeight"),
        }
        tf = attr(c, "Transform2")
        row["transform"] = [round(float(x), 6) for x in list(tf.ArrayData)] if tf is not None else None
        rows.append(row)
    report["components"] = rows

    # 也记录一下配合，看有没有明显的定位配合（晚绑定可能不暴露 FirstFeature，容错）
    mates = []
    try:
        feat = doc.FirstFeature()
        while feat is not None:
            t = str(attr(feat, "GetTypeName2") or "")
            if "Mate" in t or "配合" in t:
                mates.append({"name": str(attr(feat, "Name")), "type": t,
                              "error": attr(feat, "GetErrorCode")})
            feat = feat.GetNextFeature()
    except Exception as exc:
        report["mates_error"] = str(exc)[:200]
    report["mates"] = mates

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written:", OUT)
    print("components:", len(rows), "| fixed:", sum(1 for r in rows if r["is_fixed"]))
    print("mates:", len(mates))


if __name__ == "__main__":
    main()
