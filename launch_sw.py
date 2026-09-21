#!/usr/bin/env python3
"""用 Flow 官方 RunProduct2 启动 SolidWorks + Flow，然后确认门禁通过。

走的是官方启动路径（不是会崩的 Activator.CreateInstance 冷启动）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pythoncom
import win32com.client as win32

ROOT = Path(r"C:\Users\liujunxiang\Desktop\流体仿真2")
SOLIDWORKS_EXE = Path(r"D:\Program Files\2026SW\SOLIDWORKS\SLDWORKS.exe")
BINCFW = Path(r"D:\Program Files\2026SW\SOLIDWORKS Flow Simulation\binCFW")

sys.path.insert(0, str(ROOT))
import flow_transfer as ft


def main():
    pythoncom.CoInitialize()
    if not SOLIDWORKS_EXE.is_file():
        raise SystemExit("missing %s" % SOLIDWORKS_EXE)
    if not BINCFW.is_dir():
        raise SystemExit("missing %s" % BINCFW)

    api = ft.connect()

    # 已经在跑就直接用
    running = ft.safe_get(api, "Attach2RunningObject")
    if running is not None:
        print("already running, attaching")
    else:
        print("launching via RunProduct2 ...")
        running = api.RunProduct2(str(SOLIDWORKS_EXE), str(BINCFW))
        if running is None:
            raise SystemExit("RunProduct2 returned no InteractiveApplication")
        print("launched")

    # 等 COM 可见
    sw = None
    for attempt in range(90):
        try:
            sw = win32.GetActiveObject("SldWorks.Application")
        except Exception:
            sw = None
        if sw is not None:
            break
        time.sleep(2)
    if sw is None:
        raise SystemExit("SolidWorks 起来了但 COM 连不上")

    print("RevisionNumber :", sw.RevisionNumber())
    print("Visible        :", sw.Visible)
    doc = sw.ActiveDoc
    title = None
    if doc is not None:
        t = doc.GetTitle
        title = t if isinstance(t, str) else t()
    print("ActiveDoc      :", title)
    print("GATE:", "PASS" if (str(sw.RevisionNumber()).startswith("34.") and sw.Visible and doc is None) else "CHECK")


if __name__ == "__main__":
    main()
