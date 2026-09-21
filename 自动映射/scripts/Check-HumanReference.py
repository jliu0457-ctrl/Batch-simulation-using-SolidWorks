#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：核对 `config/flow_physics_reference.json` 里的参考真值还跟不跟得上根目录 `1/`。

**为什么是脚本不是测试。** 根目录 `1/` 是活跃的实验台 —— 用户会调分辨率、
调局部网格级别、反复重跑。任何「和根目录此刻的值相等」的断言都会跟着一起变红，
那不是代码坏了，是拿移动靶当基准的设计错了。所以：

  * **测试**只验证「解析器能work」+「config 自己自洽」
  * **这条脚本**在你**想同步**的时候手动跑，把漂移量摆出来

用法：
    python scripts/Check-HumanReference.py [--json 输出.json]

退出码：0 = 一致（在容差内）；1 = 有漂移，需要同步 config。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
for _p in (str(REPO_ROOT), str(MAPPING_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HUMAN_DIR = REPO_ROOT / "1"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def human_state() -> dict:
    """从根目录 `1/` 直接读，**不碰 SolidWorks**。"""
    xml = read_text(HUMAN_DIR / "1.xmlconfig")
    info = json.loads((HUMAN_DIR / "1.info.json").read_text(encoding="utf-8-sig"))

    def one(key: str):
        m = re.search(rf'<{key} value="([^"]*)"', xml)
        return m.group(1) if m else None

    seg = xml[xml.find("<Features_Local_Mesh"):xml.find("</Features_Local_Mesh>")]
    def local(key: str):
        m = re.search(rf'<{key} value="([^"]*)"', seg)
        return m.group(1) if m else None

    # 逐目标：瞬时 + 时间平均（从 Goals.DAT 算）
    goals_inst: dict[str, float] = {}
    for g in info.get("goals", []):
        goals_inst[g["goal"]["name"]] = g["goal"]["value"]
    av: dict[str, float] = {}
    windows: dict[str, int] = {}
    spreads: dict[str, float] = {}
    gd = HUMAN_DIR / "Goals.DAT"
    if gd.is_dir():
        for f in sorted(gd.glob("*.txt")):
            if f.stem.startswith("Serv") or f.stem == "global_parameters":
                continue
            rows = [l.split("\t") for l in read_text(f).splitlines()[1:] if l.strip()]
            if not rows:
                continue
            vals = [float(r[4]) for r in rows]
            w = max(50, int(len(vals) * 0.2))
            tail = vals[-w:]
            mean = sum(tail) / w
            av[f.stem] = mean
            windows[f.stem] = w
            spreads[f.stem] = (max(tail) - min(tail)) / abs(mean) if mean else 0.0

    return {
        "result_resolution": int(one("ResultResolution")) if one("ResultResolution") else None,
        "local_fluid_refinement_level": int(local("FluidCellsRefinementLevel") or -1),
        "cell_per_gap": local("CellPerGap"),
        "tolerance_criteria_value": local("ToleranceCriteriaValue"),
        "mesh": info.get("mesh", {}),
        "telemetry": info.get("telemetry", {}),
        "goals_instantaneous": goals_inst,
        "goals_time_averaged": av,
        "average_window": windows,
        "oscillation_spread_rel": spreads,
        "mtime": datetime.fromtimestamp((HUMAN_DIR / "1.info.json").stat().st_mtime).isoformat(timespec="seconds"),
    }


def compare(ref: dict, human: dict) -> dict:
    """逐项比，返回漂移清单。"""
    rows = []
    ht = ref["human_truth"]

    def add(item, config, actual, ok, note=""):
        rows.append({"item": item, "config": config, "根目录": actual,
                     "一致": ok, "note": note})

    # ① 分辨率与局部网格级别 —— 这两个不一致，后面的值全没意义
    add("ResultResolution(xmlconfig)", ref["mesh"]["result_resolution"],
        human["result_resolution"],
        ref["mesh"]["result_resolution"] == human["result_resolution"],
        "两边都是 xmlconfig 的值；日志档位 = 它 + 1")
    add("局部网格 流体细化级别", [p for p in ref["local_mesh"]["parameters"] if p["type"] == 67][0]["long"],
        human["local_fluid_refinement_level"],
        [p for p in ref["local_mesh"]["parameters"] if p["type"] == 67][0]["long"] == human["local_fluid_refinement_level"],
        "!! 不一致 = 参考真值不能用")
    add("CellPerGap", ref["local_mesh"]["known_unwritable"]["CellPerGap"]["ours"],
        int(human["cell_per_gap"] or 0),
        str(ref["local_mesh"]["known_unwritable"]["CellPerGap"]["ours"]) == str(human["cell_per_gap"]),
        "公开 API 改不了，只能 GUI 设 —— 见 config 的 known_unwritable")
    add("流体单元数", ht["cells_fluid"], human["mesh"].get("cells_fluid"),
        ht["cells_fluid"] == human["mesh"].get("cells_fluid"))

    # ② 目标值：瞬时 + 时间平均
    tol = ht.get("reference_tolerance_rel", 0.01)
    for name in ht["goals_si"]:
        for label, key in (("瞬时", "goals_si"), ("时间平均", "time_averaged_goals_si")):
            want = ht[key].get(name)
            got = (human["goals_instantaneous"] if label == "瞬时" else human["goals_time_averaged"]).get(name)
            if want is None or got is None:
                continue
            limit = 1e-6 if name == "SG CV出口静压" else tol
            rel = abs(got - want) / abs(want) if want else abs(got)
            add(f"{name}（{label}）", want, got, rel <= limit, f"差 {rel:.3%}")
    return {"rows": rows, "tolerance_rel": tol}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="核对参考真值 vs 根目录 1/（只读）")
    ap.add_argument("--json", default="", help="把报告写成 JSON")
    ap.add_argument("--sync", action="store_true",
                    help="把参考真值同步成根目录当前这次（**先做设置校验，不一致就拒绝**）")
    args = ap.parse_args(argv)

    if not (HUMAN_DIR / "1.info.json").is_file():
        print(f"找不到 {HUMAN_DIR / '1.info.json'}", file=sys.stderr)
        return 2
    import flow_project as fpj
    ref = fpj.load_reference()
    human = human_state()
    report = {"human_dir": str(HUMAN_DIR), "human_last_modified": human["mtime"], **compare(ref, human)}

    bad = [r for r in report["rows"] if not r["一致"]]
    print(f"根目录工程最后更新：{human['mtime']}")
    print(f"容差：{report['tolerance_rel']:.0%}（除出口静压须精确）\n")
    print("%-30s %18s %18s %8s  %s" % ("项", "config", "根目录", "一致", "备注"))
    for r in report["rows"]:
        def fmt(v):
            if isinstance(v, float):
                return f"{v:.6g}"
            return str(v)
        print("%-30s %18s %18s %8s  %s" % (
            r["item"], fmt(r["config"]), fmt(r["根目录"]), "OK" if r["一致"] else "**漂移**", r["note"]))

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告 -> {args.json}")
    if args.sync:
        return sync(ref, human, report)

    print()
    if bad:
        print(f"有 {len(bad)} 项漂移。**注意**：根目录是活跃实验台，人工调网格是正常的；")
        print("但如果 `局部网格 流体细化级别` 或 `求解器分辨率` 漂了，参考真值就作废了，必须同步 config。")
        return 1
    print("一致。")
    return 0


def sync(ref: dict, human: dict, report: dict) -> int:
    """把 `human_truth` 同步成根目录当前这次 —— **但先校验设置**。

    ⚠️ **这道校验是必须的。** 实测踩过：用户在一次 res1 的实验运行之后又改回 res3，
    而当时那轮同步把参考值记成了那次实验的（`SG 力矩Z` 差 26%）。
    根目录是活跃实验台，**"最新的一次"不等于"和我们同设置的那一次"**。
    设置对不上就拒绝同步，而不是记一个不能用的参考值。
    """
    gates = {
        "ResultResolution": (ref["mesh"]["result_resolution"], human["result_resolution"]),
        "局部网格 流体细化级别": (
            [p for p in ref["local_mesh"]["parameters"] if p["type"] == 67][0]["long"],
            human["local_fluid_refinement_level"]),
    }
    bad = {k: v for k, v in gates.items() if v[0] != v[1]}
    if bad:
        print("**拒绝同步** —— 这几项的设置和 config 不一致：")
        for k, (a, b) in bad.items():
            print(f"    {k}: config={a}  根目录={b}")
        print("说明根目录现在处在别的网格配置下（多半是在做实验）。")
        print("要么等它改回来，要么先改 config 里的设置再同步。")
        return 1

    import json as _json
    path = MAPPING_ROOT / "config" / "flow_physics_reference.json"
    d = _json.loads(path.read_text(encoding="utf-8"))
    ht = d["human_truth"]
    ht["goals_si"] = human["goals_instantaneous"]
    ht["time_averaged_goals_si"] = human["goals_time_averaged"]
    ht["goal_time_average_window"] = human["average_window"]
    ht["goal_oscillation_spread_rel"] = {k: round(v, 4) for k, v in human["oscillation_spread_rel"].items()}
    ht["cells_fluid"] = human["mesh"].get("cells_fluid")
    ht["cells_solid"] = human["mesh"].get("cells_solid")
    ht["iterations"] = human["telemetry"].get("iteration")
    ht["travels"] = human["telemetry"].get("travel")
    ht["cpu_time_solver_s"] = human["telemetry"].get("cpu_time_solver_total")
    ht["run_timestamp"] = f"{human['mtime']}（Check-HumanReference.py --sync）"
    path.write_text(_json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已同步到根目录 {human['mtime']} 那次（设置校验通过）。")
    print(f"  流体单元 {ht['cells_fluid']}  travel {ht['travels']}  迭代 {ht['iterations']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
