#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：拿一个 run 的装配体，**从指定的 .fwp 模板建一个工程**，把带过来的东西全 dump 出来。

为什么需要它：`.fwp` 是 Flow 内部的二进制容器（魔数 `ed060000`，和 `1.geom` 同族，
没有 zlib/明文），**只能建出来看**。我们要知道模板到底带了什么：

  * 网格设置（`CellPerGap` / `RefLevel` / `RefMaxCells` / `ResultResolution`）
  * 收敛设置（`CriteriaPercentage` = 0.5%？）
  * **有没有把人工的 BC / 目标 / 局部网格也带过来** —— 如果带了，
    它们的几何引用会指向**人工那套组件**，在我们的副本里是悬空的。
    这决定了我们是"直接用"还是"建完再删掉重建"。

**不保存装配体**，所以磁盘上的原工程不受影响。但 `UpdateConfigAndDataFiles()`
会往**新工程自己的目录**写 xmlconfig —— 用的是新工程名，和已有工程不冲突。

用法：
    python scripts/Probe-TemplateContents.py <runName> "<fwp 路径>"
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
for _p in (str(REPO_ROOT), str(MAPPING_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_project as fpj   # noqa: E402
import flow_session as fses  # noqa: E402
import sw_api as swa         # noqa: E402

PROBE_PROJECT = "模板探测"
KEYS = ("CellPerGap", "RefLevel", "RefMaxCells", "ResultResolution",
        "FluidCellsRefinementLevel", "PartialCellsRefinementLevel",
        "RefineAllFluidCells", "ToleranceCriteriaValue", "MaxThinChannelRefinementLevel",
        "UseConv", "UseManualMaxDV", "MaxDV", "MaxIter", "UseMaxIter")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    run, fwp = Path(fses.RUNS_ROOT) / argv[0], Path(argv[1])
    if not fwp.is_file():
        print(f"找不到模板 {fwp}", file=sys.stderr)
        return 2
    out_path = MAPPING_ROOT / "_analysis" / f"template_probe_{argv[0]}.json"

    session = fses.SwSession.attach()
    report: dict = {"run": str(run), "fwp": str(fwp), "probe_project": PROBE_PROJECT}
    try:
        report["gate"] = session.gate_or_raise(run=run)
        assembly = next(p for p in run.glob("*.SLDASM") if not p.name.startswith("~$"))
        document = fpj.open_assembly(session, assembly, "开度45°")
        configuration = fpj.active_configuration(document, "开度45°")
        report["projects_before"] = [str(n) for n in (configuration.GetProjectNames() or [])]

        built = fpj.create_project(document, configuration, fwp, PROBE_PROJECT)
        project_dir = Path(built["report"]["project_directory"])
        report["created"] = built["report"]
        project = built["project"]
        # ⚠️ `UpdateConfigAndDataFiles` 在 **FDAProject** 上，不在 ModelDoc 上 ——
        # 写到 ModelDoc 上会得到 `<unknown>.UpdateConfigAndDataFiles`（实测踩过）。
        project.UpdateConfigAndDataFiles()

        # 带过来的特征
        features = []
        enum = project.EnumFeatures()
        if enum is not None:
            enum.Reset()
            for _ in range(2000):
                f = enum.Next()
                if f is None:
                    break
                e: dict = {"name": str(swa.safe_get(f, "Name", default="") or "")}
                try:
                    e["type"] = int(f.Type)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    topo = f.GetInterface("ITopologyBasedFeature")
                    if topo is not None:
                        e["references"] = [str(r) for r in (topo.GetReferencesNames() or [])]
                except Exception:  # noqa: BLE001
                    pass
                features.append(e)
        report["features"] = features
        report["feature_count"] = len(features)

        # 新工程写出来的 xmlconfig
        xml_path = project_dir / "1.xmlconfig"
        if xml_path.is_file():
            t = xml_path.read_text(encoding="utf-8", errors="replace")
            lm = t[t.find("<Features_Local_Mesh"):t.find("</Features_Local_Mesh>")]
            conv = t[t.find("<ConvergenceOptions"):t.find("</ConvergenceOptions>")]
            report["xml"] = {
                "local_mesh": {k: (re.search(rf'<{k} value="([^"]*)"', lm).group(1)
                                   if re.search(rf'<{k} value="([^"]*)"', lm) else None) for k in KEYS},
                "criteria": re.findall(r'<CriteriaPercentage value="([^"]*)"', conv),
                "criteria_types": re.findall(r'<CriteriaType value="([^"]*)"', conv),
                "local_mesh_blocks": t.count("<Features_Local_Mesh"),
                "bc_blocks": t.count("<Boundary_Condition "),
                "goal_blocks": t.count("<Surface_Goal "),
            }
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # 不保存装配体；只关掉自己开的文档
        try:
            session.close_all_documents()
        finally:
            session.unload()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报告 -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
