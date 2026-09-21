#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：搞清 `FDAGoalCalculationResults` 到底能读出哪几种「值」。

背景：Flow 的 GUI「目标图」表里有四列 —— **数值 / 平均值 / 最小值 / 最大值**。
我们写训练行时取的是「时间平均」，但**自己按尾窗算的**，和 Flow 自己那个「平均值」
不是同一个数（实测差 0.17~0.68%）。既然人工的参照表用的是 Flow 那一列，
就该读 Flow 那一列，而不是自己算一个。

三个候选 API（官方帮助里只有这三个）：
    GetLastCalculatedValue()   —— 明确是"最后一次计算的值"（瞬时）
    GetValues()                —— "每个迭代的值"（数组）
    GetValues2()               —— 同上，但返回 variant

问题：平均值/最小值/最大值在不在 `GetValues2` 里？

用法：
    python scripts/Probe-GoalValues.py <1.fld 路径> [输出.json]
    python scripts/Probe-GoalValues.py                      # 默认取最新的一个 run

只读：**不打开任何文档** —— `LoadFDAResultFile` 直接读 .fld 文件。
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
for _p in (str(REPO_ROOT), str(MAPPING_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_geometry as fg  # noqa: E402


def latest_fld() -> Path | None:
    pats = glob.glob(str(MAPPING_ROOT / "working" / "seven_variable_trials" / "*" / "1" / "*.fld"))
    flds = [Path(p) for p in pats if not Path(p).name.startswith("r_")]
    return max(flds, key=lambda p: p.stat().st_mtime) if flds else None


def goals_dat_av(fld: Path) -> dict:
    """从同目录的 Goals.DAT 读 Flow 自己写的 AvValue / MinValue / MaxValue（做个对照）。"""
    out = {}
    gd = fld.parent / "Goals.DAT"
    if not gd.is_dir():
        return out
    for f in sorted(gd.glob("*.txt")):
        if f.stem.startswith("Serv") or f.stem == "global_parameters":
            continue
        rows = [l.split("\t") for l in f.read_text(encoding="utf-8", errors="replace").splitlines()[1:] if l.strip()]
        if not rows:
            continue
        last = rows[-1]
        out[f.stem] = {"Value": float(last[4]), "AvValue": float(last[5]),
                       "MinValue": float(last[6]), "MaxValue": float(last[7]),
                       "Delta": float(last[8]), "Criteria": float(last[9])}
    return out


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    fld = Path(argv[0]) if argv else latest_fld()
    if fld is None or not fld.is_file():
        print("找不到 .fld —— 给一个路径", file=sys.stderr)
        return 2
    out_path = Path(argv[1]) if len(argv) > 1 else (MAPPING_ROOT / "_analysis" / "goal_values_probe.json")

    import flow_transfer as ft
    import sw_api as swa
    nca = ft.connect()
    report: dict = {"fld": str(fld), "dat": goals_dat_av(fld), "api": {}}
    try:
        handler = nca.LoadFDAResultFile(str(fld), True)
        if handler is None:
            raise RuntimeError(f"LoadFDAResultFile({fld}) 返回空")
        enum = handler.GetGoalsCalculationResults2().GetGoalsEnum()
        enum.Reset()
        for _ in range(1000):
            goal = enum.Next()
            if goal is None:
                break
            name = str(goal.GetGoalName())
            if name not in fg.TRAINING_COLUMNS[7:14]:
                continue
            entry: dict = {}
            try:
                entry["GetLastCalculatedValue"] = float(goal.GetLastCalculatedValue())
            except Exception as exc:  # noqa: BLE001
                entry["GetLastCalculatedValue"] = f"EXC {type(exc).__name__}: {exc}"
            for meth in ("GetValues", "GetValues2"):
                try:
                    raw = getattr(goal, meth)()
                except Exception as exc:  # noqa: BLE001
                    entry[meth] = f"EXC {type(exc).__name__}: {exc}"
                    continue
                d = swa.safe_get(raw, "ArrayData", default=None)
                seq = list(d) if d is not None else (list(raw) if isinstance(raw, (list, tuple)) else None)
                if seq is None:
                    entry[meth] = {"type": type(raw).__name__, "repr": repr(raw)[:200]}
                    continue
                # 每个元素可能是标量，也可能是 (value, av, min, max) 这种子数组
                sample = seq[-1]
                entry[meth] = {
                    "n": len(seq),
                    "last_element_type": type(sample).__name__,
                    "last_element": repr(sample)[:200],
                    "last3": [repr(x)[:60] for x in seq[-3:]],
                }
            report["api"][name] = entry
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            nca.UnloadProductAPI()
        except Exception:  # noqa: BLE001
            pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"fld = {fld}")
    for name, e in report.get("api", {}).items():
        print(f"\n--- {name}")
        dat = report["dat"].get(name) or {}
        if dat:
            print("   Goals.DAT:  Value=%.6g  AvValue=%.6g  Min=%.6g  Max=%.6g" % (
                dat["Value"], dat["AvValue"], dat["MinValue"], dat["MaxValue"]))
        for k, v in e.items():
            if isinstance(v, dict):
                print(f"   {k}: n={v.get('n')}  末元素 {v.get('last_element_type')} = {v.get('last_element')}")
            else:
                print(f"   {k}: {v}")
    if report.get("error"):
        print("\n错误:", report["error"])
    print(f"\n报告 -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
