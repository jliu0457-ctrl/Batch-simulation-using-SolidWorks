#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：枚举**根目录人工原件**装配体的全部面，用来解 Flow 的面 ID。

背景（2026-09-21 破解）：
  Flow 工程里的 `Faces_Keys` / `1.fbd` 的 ID 是**全局顺序面号**，
  布局是「按组件切段」，每段 `起始ID + 面数`：

      03阀体 1..515 · 11蝶板 516..570 · 09密封圈 571..574
      10压板 575..615 · 08大垫片 616..619 · 04阀轴 620..661

  实测证实（我们自己的三个锚点逐位吻合）：
      **面 ID = 组件基址 + SolidWorks 面序号（0 基）**
  —— 也就是说 `08大垫片` 的 `616/617` 就是它的第 0、第 1 个面。

  但**人工原件和 V6 模板的零件文件不是同一份**（md5 不同），面的排列顺序未必一致，
  所以「第 0 个面到底是哪一个」必须去**原件**上量，不能拿模板的 dump 套。

用法：
    python scripts/Probe-HumanFaces.py [输出.json]

只读：开装配体 → 量面 → **不保存** → 关掉自己开的文档 → 卸 Flow API。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
for _p in (str(REPO_ROOT), str(MAPPING_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_project as fpj  # noqa: E402
import flow_session as fses  # noqa: E402
import sw_api as swa         # noqa: E402

HUMAN_ASSEMBLY = REPO_ROOT / '8“D94R3Y-CL600C-00组装图.SLDASM'
#: ID 表里各零件的基址（来自 `1.fbd`，人工与我们的两份逐位相同）。
BASE = {"03阀体": 1, "11蝶板": 516, "09密封圈": 571,
        "10压板": 575, "08大垫片": 616, "04阀轴": 620}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out_path = Path(argv[0]) if argv else (MAPPING_ROOT / "_analysis" / "human_root_faces.json")

    session = fses.SwSession.attach()
    report: dict = {"assembly": str(HUMAN_ASSEMBLY)}
    opened_by_us = False
    try:
        report["gate"] = session.gate_or_raise()
        fpj.open_assembly(session, HUMAN_ASSEMBLY, "开度45°")
        opened_by_us = True
        # ⚠️ 必须走 **pywin32 那条通道**（`session.sw.ActiveDoc`）。
        # Flow API 的 `session.interactive.ActiveDocument` 是 NIK 的 ModelDoc 包装，
        # 晚期绑定下**没有** `GetComponents`（那是 IAssemblyDoc 的）——
        # 症状是拿到空组件表而且不报错，看起来像"装配体里没有零件"。
        document = session.sw.ActiveDoc
        if document is None:
            raise RuntimeError("sw.ActiveDoc 为空 —— 文档没开成")

        comps = [c for c in swa.component_list(document) if c]
        report["diagnostics"] = {
            "doc_name": str(swa.safe_get(document, "Name", default="") or ""),
            "doc_class": type(document).__name__,
            "active_config": str(swa.safe_get(
                swa.safe_get(document, "ActiveConfiguration", default=None), "Name", default="") or ""),
            "n_toplevel_only": len(swa.component_list(document, top_level_only=True)),
            "n_all": len(comps),
        }
        try:
            report["diagnostics"]["raw_getcomponents"] = repr(document.GetComponents(False))[:200]
        except Exception as exc:  # noqa: BLE001
            report["diagnostics"]["raw_getcomponents"] = f"{type(exc).__name__}: {exc}"
        try:
            report["diagnostics"]["members"] = [n for n in dir(document) if "omponent" in n][:20]
        except Exception as exc:  # noqa: BLE001
            report["diagnostics"]["members"] = f"{type(exc).__name__}: {exc}"
        report["component_names"] = [str(swa.safe_get(c, "Name2", default="") or "") for c in comps]

        faces: dict[str, list[dict]] = {}
        for comp in comps:
            name = str(swa.safe_get(comp, "Name2", default="") or "")
            token = next((t for t in BASE if t in name), None)
            if token is None:
                continue
            rows = []
            for idx, rec in enumerate(swa.face_local_records(comp)):
                entry: dict = {"index": idx, "id": BASE[token] + idx,
                               "area_mm2": round(rec["area_m2"] * 1e6, 3)}
                if rec["is_plane"]:
                    p = rec["plane_params"]
                    entry["kind"] = "plane"
                    entry["normal"] = [round(float(v), 4) for v in p[:3]]
                    entry["d_m"] = round(float(p[3] if len(p) > 3 else 0.0), 5)
                elif rec["is_cone"]:
                    cp = rec["cone_params"]
                    entry["kind"] = "cone"
                    entry["radius_mm"] = round(float(cp[6]) * 1000, 4)
                    entry["half_angle_deg"] = round(float(cp[7]) * 57.29577951308232, 4)
                elif rec["is_cylinder"]:
                    cp = rec["cylinder_params"]
                    entry["kind"] = "cylinder"
                    entry["radius_mm"] = round(float(cp[6]) * 1000, 4)
                else:
                    entry["kind"] = "other"
                rows.append(entry)
            faces[name] = rows
        report["faces"] = faces
        report["counts"] = {k: len(v) for k, v in faces.items()}
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if opened_by_us:
                session.close_all_documents()
            else:
                report["close_skipped"] = "本轮没开文档（多半门禁没过），不动用户的文档"
        finally:
            session.unload()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "counts": report.get("counts"),
                      "error": report.get("error")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
