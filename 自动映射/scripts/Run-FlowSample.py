#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一个【几何已验收】的样本副本，一路跑到一行训练数据（契约见 flow_geometry.TRAINING_COLUMNS）。

    自动映射/working/seven_variable_trials/<run>/     ← 由 Run-OneDesign.exe 产出
        mapping_result.json (status=geometry_verified) + 装配体 + 6 个核心零件
                            ↓  Run-FlowSample.py
        S0 门禁 → S1 开装配体 → S2 CAD 重建 → S3 开口识别 → S4 Create Lids
        S5 封盖↔开口关联+命名 → S6 新建 Flow 工程 → S7 写 2BC+目标
        S8 内部域门禁 → S9 网格+收敛 → S10 求解 → S11 读 FLD → S12 出训练行

用法::

    python scripts/Run-FlowSample.py <runName>                     # 预检，只读
    python scripts/Run-FlowSample.py <runName> --dump-faces        # 只读：导出全部面（世界系）
    python scripts/Run-FlowSample.py <runName> --execute           # 跑到求解前一步
    python scripts/Run-FlowSample.py <runName> --execute --yes-solve
    python scripts/Run-FlowSample.py <runName> --execute --resume

**只附加，不冷启动 SolidWorks。** 跑之前先手工把 SolidWorks 启动到空白主界面。

⚠️ 关于七个目标：其中 **2 个挂封盖、3 个也挂封盖、4 个挂在密封面上**
（蝶板 / 大垫片 / 密封圈 / 阀轴 —— 见 plan §1.4）。密封面目前**还没有稳定名字**，
所以默认遇到它们会停下并报 ``needs_seat_faces``；先用 ``--dump-faces`` 导出面数据
把规则补完，或用 ``--allow-partial-goals`` 先出一行「3 个目标有值、4 个留空」的合法样本
（契约允许留空，**禁止补零**）。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
SCRIPTS = MAPPING_ROOT / "scripts"
for _p in (str(REPO_ROOT), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_geometry as fg          # noqa: E402
import flow_project as fpj          # noqa: E402
import flow_session as fses         # noqa: E402
import sw_api as swa                # noqa: E402  晚期绑定的兼容层，坑都集中在那里

RUNS_ROOT = fses.RUNS_ROOT
DEFAULT_FWP = Path(r"C:\ProgramData\COSMOS Applications\Flow Simulation 2026\template\internal_water.fwp")
PROJECT_NAME = "流体力学仿真"
TRAINING_XLSX = MAPPING_ROOT / "outputs" / "training_dataset.xlsx"

#: 会写盘、因此要记哈希的阶段 —— --resume 靠它确认「还是同一份几何」
#: ⚠️ cap_binding 也算：它会给封盖内面打名字并**保存零件**（名字存在零件文档里，
#: 不保存就随文档关闭消失），所以磁盘几何会变。漏掉它的话续跑必然被自己拒掉。
#: ⚠️ persist 是 S9 之后那次「把工程落盘」的保存（原先只在求解路径里做，现在
#: 提前到分支之前）。它同样会改写全部零件/装配体文件 —— 不记哈希的话，续跑守卫
#: 会拿 cap_binding 的旧哈希去比，被自己拒掉。
MUTATING_STAGES = ("cad_rebuild", "lids", "cap_binding", "persist", "solve")
STAGE_ORDER = (
    "gate", "open_assembly", "cad_rebuild", "openings", "lids", "cap_binding",
    "project", "features", "internal_gate", "mesh", "goal_criteria", "persist",
    "solve", "results", "training_row",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ================================================================= 坐标变换
#
# `IComponent2.Transform2.ArrayData` 是 **16 项、列主序**：
#     data[0..2] = 局部 X 轴在装配体里的方向
#     data[3..5] = 局部 Y 轴
#     data[6..8] = 局部 Z 轴
#     data[9..11] = 原点
#     data[12..15] = (1,0,0,0)，本用途用不到
# 实测核对（_analysis/co_11蝶板.json 的 final_transforms）：
#   入口管道 data = [0,0,1, 0,1,0, -1,0,0, -0.115,0,0, 1,0,0,0]
#     → 局部 Z 映射到世界 -X，管子沿 +Z 伸出 0.5 m → 世界 x=-0.615 ✓ 与圆边数据一致
#   11蝶板   → 绕 Z 的 45° 旋转，即"开度45°" ✓
# 注意：这与 Diag-MateGeometry.ps1:33-36 记的「t[0..2]=X, t[3..5]=Y, t[6..8]=Z,
# t[9..11]=origin」是同一件事（那三组就是三个列向量），不是两套布局。

def transform_point(t: list[float], p) -> list[float]:
    x, y, z = (float(v) for v in p[:3])
    return [
        t[0] * x + t[3] * y + t[6] * z + t[9],
        t[1] * x + t[4] * y + t[7] * z + t[10],
        t[2] * x + t[5] * y + t[8] * z + t[11],
    ]


def transform_vector(t: list[float], v) -> list[float]:
    x, y, z = (float(a) for a in v[:3])
    out = [
        t[0] * x + t[3] * y + t[6] * z,
        t[1] * x + t[4] * y + t[7] * z,
        t[2] * x + t[5] * y + t[8] * z,
    ]
    n = math.sqrt(sum(a * a for a in out)) or 1.0
    return [a / n for a in out]


def array_data(obj) -> list[float]:
    """兼容旧调用；变换解析统一由 ``sw_api`` 负责。"""
    return swa.transform_array(obj)


def transformed_box(t: list[float], box) -> list[float]:
    """局部包围盒的 8 个角都变换过去再取包围盒。

    无旋转时就是平移后的盒；有旋转时这是外包围盒 —— 够用，因为我们只用它
    判「薄轴」和「在哪一侧」。
    """
    lo, hi = [float(v) for v in box[:3]], [float(v) for v in box[3:6]]
    corners = [[lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1], lo[2] if i & 4 else hi[2]]
               for i in range(8)]
    pts = [transform_point(t, c) for c in corners]
    return [min(p[i] for p in pts) for i in range(3)] + [max(p[i] for p in pts) for i in range(3)]


# ================================================================= 状态

def state_path(run: Path) -> Path:
    return Path(run) / "flow_sample_state.json"


def load_state(run: Path) -> dict:
    path = state_path(run)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema_version": 1, "run": str(run), "stages": {}, "started_utc": now()}


def save_state(run: Path, state: dict) -> None:
    state["updated_utc"] = now()
    state_path(run).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _record(state: dict, run: Path, stage: str, extra: dict | None = None) -> None:
    state.setdefault("stages", {})[stage] = {"ok": True, "at_utc": now(), **(extra or {})}
    save_state(run, state)


def _done(state: dict, stage: str) -> bool:
    return bool(state.get("stages", {}).get(stage, {}).get("ok"))


#: `--resume` 时**必须跳过**的阶段 —— 它们不是幂等的，重跑会毁掉现场：
#:   S4 Create Lids 再跑一次 = 8 个封盖
#:   S6 新建工程 再跑一次 = 把已写好的特征全删了重建
#:   S7 写特征   再跑一次 = 每个目标/边界都来两份
#:   S10 求解    再跑一次 = 白等几十分钟
#: 其余阶段（开装配体、重建、识别、门禁、网格设置）重跑无害，就重跑 —— 顺便刷新现场状态。
NON_IDEMPOTENT_STAGES = ("lids", "cap_binding", "project", "features", "solve",
                         "results", "training_row")


def assembly_of(run: Path) -> Path:
    found = [p for p in Path(run).glob("*.SLDASM") if not p.name.startswith("~$")]
    if len(found) != 1:
        raise RuntimeError(
            f"{run} 下应有恰好 1 个装配体，实际 {len(found)} 个：{[p.name for p in found]} —— "
            "多于一个通常是残留的 ~$ 锁文件")
    return found[0]


def hash_many(paths) -> dict[str, str]:
    """已存在的文件 → sha256。**跳过 `~$*` 锁文件**，见 `geometry_hashes`。"""
    return {Path(p).name: fses.sha256_of(Path(p))
            for p in paths
            if Path(p).is_file() and not Path(p).name.startswith("~$")}


def geometry_hashes(run: Path) -> dict[str, str]:
    """几何哈希，用于 `--resume` 判断「还是同一份几何」。

    ⚠️ **必须排除 `~$*` 锁文件。** `*.SLDPRT` 会匹配到 `~$xxx.SLDPRT` 那种 12 字节的
    残留锁文件；它们在文档打开时存在、关闭后消失，于是记录下来的哈希表**永远**
    对不上磁盘 —— 每一次 `--resume` 都会被自己的守卫拒掉，而 7 个真实 CAD 文件
    其实一个字节都没动。实测踩过。
    """
    run = Path(run)
    return hash_many([assembly_of(run)] + sorted(run.glob("*.SLDPRT")))


def purge_project_dir(run: Path) -> dict:
    """删掉 `<run>/1`（Flow 工程目录），为重建工程让路。

    **只删这一个路径**，而且必须先确认它就在 run 目录下、run 又在 trials 目录下 ——
    绝不做任何通配删除。里面装的是上一轮的工程文件（`.xmlconfig`/`.cpt`/`.fld`），
    重建时会由 Flow 重新生成，没有需要保留的东西。
    """
    run = Path(run).resolve()
    if RUNS_ROOT != run and RUNS_ROOT not in run.parents:
        raise RuntimeError(f"拒绝清理：{run} 不在 {RUNS_ROOT} 之下")
    target = run / "1"
    if target.resolve().parent != run or target.name != "1":
        raise RuntimeError(f"拒绝清理：{target} 不是 run 目录下的 '1'")
    if not target.exists():
        return {"purged": False, "path": str(target), "reason": "不存在"}
    removed = sorted(p.name for p in target.rglob("*") if p.is_file())[:200]
    import shutil
    shutil.rmtree(target)
    return {"purged": True, "path": str(target), "file_count": len(removed)}


def resume_is_safe(run: Path, state: dict) -> tuple[bool, str]:
    """续跑前必须确认磁盘几何仍是记录时那一份。**哈希变了就拒绝续跑。**

    ⚠️ 只比对**最后一个已完成的改动几何的阶段**，不能每个阶段都比 ——
    `cad_rebuild` 记录之后，`lids` 会往装配体里加四个封盖**并保存**，
    装配体哈希必然改变。那是正常的。拿 `cad_rebuild` 的旧记录去比，
    等于每次续跑都必然被自己拒掉（实测踩过）。
    """
    latest = None
    for stage in MUTATING_STAGES:          # 按顺序走，最后一个有记录的才算数
        if state.get("stages", {}).get(stage, {}).get("hashes"):
            latest = stage
    if latest is None:
        return True, "ok（还没有任何阶段记录几何哈希）"
    hashes = state["stages"][latest]["hashes"]
    current = hash_many(Path(run) / name for name in hashes)
    missing = sorted(set(hashes) - set(current))
    changed = sorted(name for name in set(hashes) & set(current) if current[name] != hashes[name])
    if missing or changed:
        return False, (f"{latest} 之后记录的几何与磁盘不符 —— 必须删掉重跑"
                       f"（缺失 {missing}，变了 {changed}）")
    return True, f"ok（比对 {latest} 记录的 {len(hashes)} 个文件）"


# ================================================================= 输入

def find_mapping_report(run: Path) -> tuple[Path, dict]:
    """几何验收报告有两个来源，两份都要认：

    * ``<run>/mapping_result.json`` —— `Apply-SevenVariableDesign.ps1` 写的（本机
      .ps1 被执行策略拦着，跑不了）
    * ``_analysis/e2e_<run>.json`` —— `Run-OneDesign.exe` 写的（**当前唯一能跑的那条**）

    ⚠️ `Run-OneDesign.exe` 用 .NET 的 `JavaScriptSerializer` 序列化，**嵌套对象会被写成
    类型名**（`input` 变成字符串 `"SevenVariableAdapter+Design"`、`writes` 全是
    `"SevenVariableAdapter+WriteRecord"`）。所以 e2e 报告里**没有设计输入**，
    必须用 ``--design`` 显式给，或靠 ``design_input.json``。
    """
    run = Path(run)
    candidates = [run / "mapping_result.json",
                  MAPPING_ROOT / "_analysis" / f"e2e_{run.name}.json"]
    for path in candidates:
        if path.is_file():
            return path, json.loads(path.read_text(encoding="utf-8"))
    raise RuntimeError(f"找不到几何验收报告，试过：{[str(p) for p in candidates]}")


def load_design(run: Path, mapping: dict, explicit: Path | None = None) -> dict:
    """七变量设计点。优先级：``--design`` 显式文件 > ``design_input.json`` > 报告里的 input。"""
    design = None
    if explicit is not None:
        design = json.loads(Path(explicit).read_text(encoding="utf-8"))
    if design is None:
        p = Path(run) / "design_input.json"
        if p.is_file():
            design = json.loads(p.read_text(encoding="utf-8"))
    if design is None:
        candidate = mapping.get("input")
        # e2e 报告的 input 是 .NET 类型名字符串，不是设计点 —— 必须排除
        if isinstance(candidate, dict):
            design = candidate
    if not isinstance(design, dict):
        raise RuntimeError(
            "读不到七变量设计输入：请用 --design 指定（Run-OneDesign.exe 写的 e2e 报告里"
            "没有设计点，它的嵌套对象被序列化成了类型名）")
    missing = [k for k in fg.DESIGN_COLUMNS if k not in design]
    if missing:
        raise RuntimeError(f"设计输入缺 {missing}")
    return {k: float(design[k]) for k in fg.DESIGN_COLUMNS}


def assert_geometry_verified(mapping: dict, source: Path | None = None) -> None:
    """两种报告格式的验收判据。

    ``mapping_result.json`` 有 ``status`` 字段；``e2e_*.json`` 没有（它是适配器直接
    产出的），所以那种只查 ``completed`` + ``physical_mapping_verified``。
    """
    completed = mapping.get("completed") is True
    verified = mapping.get("physical_mapping_verified") is True
    status = mapping.get("status")
    ok = completed and verified and (status in (None, "geometry_verified"))
    if not ok:
        raise RuntimeError(
            f"Flow 之前必须有几何验收通过的报告（来源 {source}）："
            f"status={status!r} completed={mapping.get('completed')!r} "
            f"physical_mapping_verified={mapping.get('physical_mapping_verified')!r}")


# ================================================================= CAD 探针

def enumerate_faces(document) -> dict[str, list[dict]]:
    """枚举装配体全部组件的面，坐标转世界系。**只读**。"""
    out: dict[str, list[dict]] = {}
    for comp in swa.component_list(document):
        if not comp:
            continue
        name = str(swa.safe_get(comp, "Name2", default="") or "")
        t = swa.component_transform(comp)
        faces = []
        for rec in swa.face_local_records(comp):
            item: dict[str, Any] = {
                "area_m2": _r(rec["area_m2"]),
                "box_m": [_r(v) for v in transformed_box(t, rec["box_m"])],
                "is_plane": rec["is_plane"], "is_cone": rec["is_cone"],
                "is_cylinder": rec["is_cylinder"],
            }
            if rec["is_plane"]:
                p = rec["plane_params"]
                item["plane_params"] = [_r(v) for v in p[:3]] + [
                    _r(v) for v in transform_point(t, p[3:6])]
            if rec["is_cone"]:
                cp = rec["cone_params"]
                item["cone_params"] = {
                    "apex_world_m": [_r(v) for v in transform_point(t, cp[:3])],
                    "axis_world": [_r(v) for v in transform_vector(t, cp[3:6])],
                    "radius_m": _r(cp[6]),
                    "half_angle_deg": _r(math.degrees(cp[7])),
                }
            if rec["is_cylinder"]:
                cp = rec["cylinder_params"]
                item["cylinder_params"] = {
                    "origin_world_m": [_r(v) for v in transform_point(t, cp[:3])],
                    "axis_world": [_r(v) for v in transform_vector(t, cp[3:6])],
                    "radius_m": _r(cp[6]),
                }
            faces.append(item)
        out[name] = faces
    return out


def enumerate_open_circular_edges(document) -> list[dict]:
    """只枚举四个开口所属组件的圆边，圆心/法向/半径转世界系。

    判据抄自 `ProbeOpenCircularEdges.cs`：局部 `CircleParams` = [c..., n..., r]，
    点用 `GetTotalTransform(True)`（含平移），法向只用方向。

    `openings_from_edges()` 只读取入口管道、出口管道和 03 阀体。旧实现却把蝶板、
    密封圈、螺栓等所有组件的圆边都跨 COM 枚举一遍（基线 448 条、25~48 秒）。
    先按组件名过滤不会改变开口判据，只去掉永远不会被消费者读取的数据。
    """
    rows = []
    pipe_components = set(fg.PIPE_COMPONENT.values())
    for comp in swa.component_list(document):
        if not comp:
            continue
        name = str(swa.safe_get(comp, "Name2", default="") or "")
        if fg.base_component(name) not in pipe_components and fg.BODY_COMPONENT not in name:
            continue
        try:
            t = swa.transform_array(swa.total_transform(comp))
        except Exception:  # noqa: BLE001
            continue
        for edge in swa.circular_edges(comp):
            rows.append({
                "component": name,
                "center_m": [_r(v) for v in transform_point(t, edge["center_m"])],
                "normal": [_r(v) for v in transform_vector(t, edge["normal"])],
                "radius_m": _r(edge["radius_m"]),
                "vertices": 0 if edge["closed"] else 2,
            })
    return rows


def _r(v: float, n: int = 9) -> float:
    return round(float(v), n)


# ================================================================= Create Lids

def do_create_lids(sw, run: Path, config_name: str, openings: dict) -> dict:
    native = sw.ActiveDoc
    if native is None:
        raise RuntimeError("Create Lids 之前拿不到原生 SolidWorks 文档")
    before = {str(c.Name2) for c in (native.GetComponents(False) or [])}

    native.ClearSelection2(True)
    hits = []
    for i, logical in enumerate(fg.LOGICAL_OPENINGS):
        args = fg.ray_select_args(openings[logical], append=(i > 0))
        ok = bool(native.Extension.SelectByRay(*args))
        hits.append({"logical": logical, "selected": ok,
                     "selection_count": int(native.SelectionManager.GetSelectedObjectCount2(-1)),
                     "component": _component_under_selection(native, i + 1)})
        if not ok:
            break
    fg.check_ray_hits(hits, openings)          # S3 门禁：命中组件必须与预期一致

    flow_app = sw.GetAddInObject("FloWorks.App")
    if flow_app is None:
        raise RuntimeError('拿不到 Flow 加载项对象 sw.GetAddInObject("FloWorks.App")')
    call = flow_app.TB_CloseHole()             # 成功时返回 None —— 不能当判据
    time.sleep(1.0)
    sw.RunCommand(-2, "")                      # 接受 PropertyManager
    time.sleep(2.0)
    native.ForceRebuild3(False)
    errors, warnings = _variant_int(), _variant_int()
    saved = bool(native.Save3(1, errors, warnings))

    new = sorted({str(c.Name2) for c in (native.GetComponents(False) or [])} - before)
    cap_files = sorted(p.name for p in Path(run).glob("封盖*.SLDPRT"))
    report = {"ray_hits": hits, "create_lids_call": repr(call), "new_components": new,
              "cap_part_files": cap_files, "saved_silent": saved,
              "save_errors": int(errors.value), "save_warnings": int(warnings.value),
              "note": "TB_CloseHole 成功时返回 None；判据是新增组件数 == 4 且四个零件落盘"}
    if len(new) != 4:
        raise RuntimeError(f"Create Lids 后新增组件 {len(new)} 个（应为 4）：{new}")
    if len(cap_files) != 4:
        raise RuntimeError(f"封盖零件落盘 {len(cap_files)} 个（应为 4）：{cap_files}")
    report["ok"] = True
    return report


def _variant_int():
    import pythoncom
    import win32com.client as win32
    return win32.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)


def _component_under_selection(native, index: int) -> str:
    """读第 `index` 个已选实体所属的组件（**下标从 1 开始**）。

    ⚠️ 必须传「当前是第几个选择」，不能写死 1 —— 写死的话四次射线全读成第一个组件，
    表现为「四个开口都命中入口管道」。参考实现用的是 `index + 1`。
    """
    try:
        comp = native.SelectionManager.GetSelectedObjectsComponent4(index, -1)
        return str(comp.Name2) if comp is not None else ""
    except Exception:  # noqa: BLE001
        return ""


# ================================================================= 封盖关联

def do_bind_caps(sw, document, run: Path, config_name: str) -> dict:
    """按【几何】把封盖配到四个开口，并给内面打稳定名字。"""
    native = sw.ActiveDoc
    comps = swa.component_list(native)
    cap_components = [c for c in comps if str(swa.safe_get(c, "Name2", default="")).startswith("封盖")]
    if len(cap_components) != 4:
        raise RuntimeError(f"应有 4 个封盖组件，找到 {len(cap_components)}")

    cap_records = {str(swa.safe_get(c, "Name2")): swa.face_local_records(c)
                   for c in cap_components}
    caps = {str(swa.safe_get(c, "Name2")): _cap_faces(
        c, cap_records[str(swa.safe_get(c, "Name2"))]) for c in cap_components}
    openings = {k: {"reference_inner_plane": v} for k, v in fg.REFERENCE_INNER_PLANE.items()}
    # 按开口给容差：下开口的参照值不权威（见 flow_physics_reference.json 的说明）
    tol = {k: v for k, v in (fpj.load_reference().get("cap_reference_tolerance_m") or {}).items()
           if not k.startswith("_") and not k.endswith("_reason")} or 0.001
    paired = fg.pair_caps_to_openings(caps, openings, tol_m=tol)

    named = {}
    for logical, info in paired.items():
        comp = next(c for c in cap_components
                    if str(swa.safe_get(c, "Name2")) == info["component"])
        index = _inner_face_index(comp, info, cap_records[info["component"]])
        if index is None:
            raise RuntimeError(f"{logical}: 在 {info['component']} 上找不到内面 "
                               f"{info['inner_plane_value_m']}")
        named[logical] = swa.set_entity_name(sw, comp, index, f"FLOW_{logical.upper()}_INNER")
    native.ForceRebuild3(False)
    return {"paired": {k: {"component": v["component"],
                           "inner_plane_m": v["inner_plane_value_m"],
                           "reference_delta_m": v.get("reference_delta_m")}
                       for k, v in paired.items()},
            "named": named, "ok": True}


def _cap_faces(component, records: list[dict] | None = None) -> list[dict]:
    t = swa.component_transform(component)
    faces = []
    for rec in records if records is not None else swa.face_local_records(component):
        item: dict[str, Any] = {"component": str(swa.safe_get(component, "Name2", default="")),
                                "area_m2": rec["area_m2"],
                                "box_m": transformed_box(t, rec["box_m"]),
                                "is_plane": rec["is_plane"]}
        if rec["is_plane"]:
            p = rec["plane_params"]
            item["plane_params"] = list(p[:3]) + transform_point(t, p[3:6])
        faces.append(item)
    return faces


def _inner_face_index(component, info, records: list[dict] | None = None) -> int | None:
    """内面在 `swa.face_objects(component)` 里的序号 —— 命名和选中都要用它。

    ⚠️ 目标位置要用**封盖自己**的内面位置（`classify_cap` 算出来的），
    不是参照文件里的值。`TB_CloseHole()` 造出来的盖和模板的原生盖位置略有差异，
    拿参照值去找会找不到（实测：配对容差 1 mm 过了，但按参照值精确匹配失败）。
    """
    target = float(info["inner_plane_value_m"])
    t = swa.component_transform(component)
    for i, rec in enumerate(records if records is not None else swa.face_local_records(component)):
        if not rec["is_plane"]:
            continue
        origin = transform_point(t, rec["plane_params"][3:6])
        if any(abs(o - target) < 1e-6 for o in origin):
            return i
    return None


# ================================================================= 目标

def resolve_goal_components(spec: dict, binding: dict, native) -> tuple[list[str], str | None]:
    """给一个目标返回 (组件名候选, face_name 或 None)。"""
    face_name = None
    logical = None
    for token in spec.get("faces_on", []):
        if isinstance(token, str) and token.startswith("FLOW_") and token.endswith("_INNER"):
            logical = token[len("FLOW_"):-len("_INNER")].lower()
    if logical:
        face_name = f"FLOW_{logical.upper()}_INNER"
        comp = binding["paired"].get(logical, {}).get("component", "")
        return _cap_name_candidates(comp), face_name
    return [], None


def _cap_name_candidates(component: str) -> list[str]:
    """组件名的两种写法：装配体给 `封盖3-1` / `X^装配体-1`，Flow 引用串用 `封盖3<1>`。

    两种都得试 —— 不同版本、不同调用路径下都出现过。
    """
    out = [component]
    if "<" in component:
        return list(dict.fromkeys(c for c in out if c))
    if "^" in component:
        base, tail = component.split("^", 1)
        instance = tail.rsplit("-", 1)[-1] if "-" in tail else ""
    elif "-" in component:
        base, instance = component.rsplit("-", 1)
    else:
        base, instance = component, ""
    out.append(base)
    if instance.isdigit():
        out.append(f"{base}<{instance}>")
    return list(dict.fromkeys(c for c in out if c))


#: 密封锥面的半锥角。实测：蝶板/大垫片/密封圈/压板上的密封锥面共享 17.75°，
#: 等于设计变量 `alpha_deg` 35.5 的一半。见 flow_geometry.cone_face_indices 的文档。
#:
#: ⚠️ **这个判据已经不用来选目标面了 —— 它是错的。** 人工的 7 个目标没有一个是锥面：
#: `SG 密比压` 的 4 个面全是平面，`SG 蝶板法向压力` 也是平面。按半锥角挑出来的 3 个面
#: 全都挑错了（密比压 841 网格面 vs 人工 288）。现在按 config 的 `face_selection`
#: 用**面序号**选。这个常数保留只作为几何参考。
SEAL_CONE_HALF_ANGLE_DEG = 17.75


class ComponentCatalog:
    """Create Lids 完成后的短生命周期组件索引。

    `GetComponents(False)` 是跨进程 COM 调用。目标、局部网格和封盖修复原来每解析
    一个 token 都重新取整张组件表。S4 以后装配体拓扑不再变化，因此一次获取并在 S7
    内复用不会改变解析逻辑。不要在 Create Lids 前创建或跨拓扑修改复用。
    """

    def __init__(self, native) -> None:
        self.named = [
            (component, str(swa.safe_get(component, "Name2", default="") or ""))
            for component in swa.component_list(native) if component
        ]

    def names_for(self, tokens) -> list[str]:
        return list(dict.fromkeys(
            name for token in tokens for _, name in self.named if token in name))

    def component_by_token(self, token: str):
        for component, name in self.named:
            if token in name:
                return component
        raise RuntimeError(f"装配体里找不到含 {token!r} 的组件")

    def component_by_name(self, wanted: str):
        for component, name in self.named:
            if name == wanted:
                return component
        return None


def add_local_mesh_feature(native, project, reference: dict,
                           catalog: ComponentCatalog | None = None) -> dict:
    """建局部网格。零件名按配置里的零件号 token 在活装配体里解析。"""
    spec = reference.get("local_mesh")
    if not spec:
        return {"skipped": True, "reason": "配置里没有 local_mesh"}
    candidates = []
    for token in spec["components"]:
        names = _names_for(native, [token], catalog=catalog)
        if not names:
            raise RuntimeError(f"装配体里找不到含 {token!r} 的组件，无法建局部网格")
        group = []
        for name in names:
            group.append(name)
            base = name.split("^")[0]
            group.append(base)
            if "-" in base and base.rsplit("-", 1)[-1].isdigit():
                stem, idx = base.rsplit("-", 1)
                group.append(f"{stem}<{idx}>")
        candidates.append(list(dict.fromkeys(group)))
    return fpj.add_local_mesh(project, spec, component_candidates=candidates,
                              essential_types=spec.get("essential_parameters") or ())


def _names_for(native, tokens, *, catalog: ComponentCatalog | None = None) -> list[str]:
    if catalog is not None:
        return catalog.names_for(tokens)
    out = []
    for tok in tokens:
        for c in swa.component_list(native):
            name = str(swa.safe_get(c, "Name2", default="") or "")
            if tok in name:
                out.append(name)
    return list(dict.fromkeys(out))


def component_by_token(native, token: str, *, catalog: ComponentCatalog | None = None):
    """按零件号（如 `11蝶板`）在装配体里找组件。"""
    if catalog is not None:
        return catalog.component_by_token(token)
    for c in swa.component_list(native):
        if token in str(swa.safe_get(c, "Name2", default="")):
            return c
    raise RuntimeError(f"装配体里找不到含 {token!r} 的组件")


def make_named_face_selector(sw, native, component, entity_name: str):
    """返回一个回调：在原生 CAD 里选中**打了指定名字的那个面**，返回选中数。

    配合 `bind_faces(select=...)` 用 —— `AddFaces(use_face_name=True)` 按名字绑不上时，
    先选中再 `UpdateReferenciesFromSelection(Unit)` 是另一条路。
    """
    # bind_faces 可能按不同接口路径多次调用同一个回调。此后不再改 CAD 拓扑，
    # 因而面对象和零件文档可以只跨 COM 取得一次。
    part = swa.component_part_document(sw, component)
    faces = swa.face_objects(component)

    def _select() -> int:
        native.ClearSelection2(True)
        if part is None:
            return 0
        total = 0
        for obj in faces:
            try:
                if str(part.GetEntityName(obj) or "") != entity_name:
                    continue
            except Exception:  # noqa: BLE001
                continue
            if swa.select_face(obj, append=total > 0):
                total += 1
        return total
    return _select


class FaceSignatureMismatch(RuntimeError):
    """按序号选中的面与配置里记的**类型**不符 —— 说明零件的拓扑变了。

    宁可在这里炸掉，也不要静默把错的面绑到目标上：错面的目标值照样算得出来，
    只是整批训练数据是错的（实测的教训：按半锥角挑密封面，三个面全挑错，
    密比压 841 个网格面 vs 人工 288，7 个目标整体偏 3~15%）。
    """


def _face_kind(rec: dict) -> str:
    if rec.get("is_plane"):
        return "plane"
    if rec.get("is_cone"):
        return "cone"
    if rec.get("is_cylinder"):
        return "cylinder"
    return "other"


def make_face_index_selector(native, selection, *, catalog: ComponentCatalog | None = None,
                             face_cache: dict | None = None):
    """返回一个回调：按 **(组件零件号, SolidWorks 面序号)** 在原生 CAD 里选面。

    ⚠️ 序号不是猜的。Flow 工程里 `Faces_Keys`/`1.fbd` 的面 ID =
    **组件基址 + 面序号**（基址表见 config 的 `face_id_cipher`，用四个独立锚点验证过），
    人工那 7 个目标的面就是这么定下来的。我们自己选的面 ID 也逐位对得上
    （大垫片面<3>→619、蝶板面<53>→569、密封圈面<3>→574、封盖4面<1>→686）。

    `selection` 是 `[{"component","indices","kinds","baseline_area_mm2"}, ...]`：
    `kinds` 是**硬判据**（不符就抛），`baseline_area_mm2` 只记比值 ——
    设计变量会改面积，面积当不了判据。
    """
    def _select() -> int:
        native.ClearSelection2(True)
        total, picked, diag = 0, [], []
        for entry in selection:
            token = entry["component"]
            indices = list(entry["indices"])
            kinds = list(entry.get("kinds") or [None] * len(indices))
            areas = list(entry.get("baseline_area_mm2") or [None] * len(indices))
            cached = face_cache.get(token) if face_cache is not None else None
            if cached is None:
                comp = (catalog.component_by_token(token) if catalog is not None
                        else component_by_token(native, token))
                cached = (swa.face_objects(comp), swa.face_local_records(comp))
                if face_cache is not None:
                    face_cache[token] = cached
            objects, records = cached
            for k, idx in enumerate(indices):
                want = kinds[k] if k < len(kinds) else None
                base = areas[k] if k < len(areas) else None
                if idx >= len(objects) or idx >= len(records):
                    raise FaceSignatureMismatch(
                        f"{token} 只有 {len(objects)} 个面，配置要第 {idx} 个 —— 零件拓扑变了")
                rec = records[idx]
                got = _face_kind(rec)
                area_mm2 = rec["area_m2"] * 1e6
                diag.append({
                    "component": token, "index": idx, "kind": got, "want_kind": want,
                    "area_mm2": round(area_mm2, 3), "baseline_area_mm2": base,
                    "area_ratio": round(area_mm2 / base, 4) if base else None,
                })
                if want and got != want:
                    raise FaceSignatureMismatch(
                        f"{token} 第 {idx} 个面是 {got}，配置要 {want} —— 零件拓扑变了，"
                        "按序号绑会绑错面，停在这里比出一批错数据好")
                if swa.select_face(objects[idx], append=total > 0):
                    total += 1
                    picked.append(f"{token}#{idx}")
        _select.last_picked = picked
        _select.diagnostic = diag
        return total
    _select.last_picked = []
    _select.diagnostic = []
    return _select


def _cap_face_selector(sw, native, binding: dict, logical: str, face_name: str,
                       *, catalog: ComponentCatalog | None = None):
    """封盖面也能走「现场选中」那条路 —— `AddFaces` 按名字绑不上时的备用。"""
    comp_name = binding.get("paired", {}).get(logical, {}).get("component")
    if not comp_name:
        return None
    if catalog is not None:
        comp = catalog.component_by_name(comp_name)
    else:
        comp = next((c for c in swa.component_list(native)
                     if str(swa.safe_get(c, "Name2", default="")) == comp_name), None)
    if comp is None:
        return None
    return make_named_face_selector(sw, native, comp, face_name)


def repair_template_features(project, reference: dict, binding: dict, native, sw, survey: dict,
                             *, catalog: ComponentCatalog | None = None) -> dict:
    """模板带过来的特征里，**挂封盖上的那几个引用是断的** —— 删掉，用我们自己的机制重建。

    ⚠️ **为什么是"删了重建"而不是"原地重绑"**：`AddFaces` 是给**新建**特征用的接口。
    对已经存在的特征调它，要么报 `E_INVALIDARG(-2147024809)`，要么"返回成功但引用为空"
    —— 两条路都试过（实测 2026-09-21），没有第三条。所以只能删了重来。

    ⚠️ **为什么只删这 5 个、其余 5 个必须留着**：
      * **局部网格**是 `CellPerGap = 12` **唯一的载体** —— 它存在**那个特征里**，
        不在工程级设置里。删了重建就拿不到 12（公开 API 写不了这个参数，
        实测 16 个参数里没有它）。而且它 `Rebuild` 是**成功**的。
      * 蝶板/垫片/密封圈/阀轴上的 4 个目标用的是**人工原版的引用**，比我们自己
        按面序号选的更权威。它们也 Rebuild 成功。

    为什么只有封盖会断：**我们的封盖是 `TB_CloseHole` 现造的，和人工手建的不是同一个
    几何** —— 组件名对得上（`封盖1<1>`），`面<1>` 解析不到。
    """
    specs: dict[str, tuple[dict, str]] = {}
    for b in reference["boundaries"]:
        specs[str(b["name"])] = ({**b, "faces_on": [f"FLOW_{str(b['role']).upper()}_INNER"]},
                                 "boundary")
    for g in reference["goals"]:
        specs[str(g["name"])] = (dict(g), "goal")

    broken = [n for n, e in (survey.get("found") or {}).items() if not e.get("rebuild")]
    objects: dict[str, object] = {}
    enum = project.EnumFeatures()
    if enum is not None:
        enum.Reset()
        for _ in range(2000):
            f = enum.Next()
            if f is None:
                break
            nm = str(swa.safe_get(f, "Name", default="") or "")
            if nm in broken:
                objects[nm] = f

    repaired, still_broken = [], []
    for name in broken:
        entry: dict = {"name": name}
        pair = specs.get(name)
        feature = objects.get(name)
        if pair is None or feature is None:
            entry["reason"] = "找不到对应的规格或特征对象"
            still_broken.append(entry)
            continue
        spec, kind = pair
        faces_on = [t for t in spec.get("faces_on", ()) if str(t).startswith("FLOW_")]
        if not faces_on:
            entry["reason"] = "不是封盖面 —— 不在本函数的范围（那几个实测都 Rebuild 成功）"
            still_broken.append(entry)
            continue
        face_name = faces_on[0]
        logical = face_name[len("FLOW_"):-len("_INNER")].lower()
        candidates, resolved = resolve_goal_components(spec, binding, native)
        if not resolved:
            resolved = face_name
        entry["face_name"] = face_name
        # ① 删
        try:
            entry["deleted"] = bool(feature.Delete())
        except Exception as exc:  # noqa: BLE001
            entry["deleted"] = False
            entry["delete_error"] = f"{type(exc).__name__}: {exc}"
        # ② 重建 —— 走和"自己写特征"完全相同的那条路（已验证过几十条）
        try:
            if kind == "boundary":
                entry["created"] = fpj.add_boundary(
                    project, spec, component_candidates=candidates, face_name=face_name,
                    select=_cap_face_selector(sw, native, binding, logical, face_name,
                                              catalog=catalog))
            else:
                entry["created"] = fpj.add_surface_goal(
                    project, spec, component_candidates=candidates, face_name=face_name,
                    select=_cap_face_selector(sw, native, binding, logical, face_name,
                                              catalog=catalog))
        except Exception as exc:  # noqa: BLE001
            entry["created"] = None
            entry["create_error"] = f"{type(exc).__name__}: {exc}"
        created = entry.get("created") or {}
        refs = ((created.get("references") or {}).get("references")) or []
        entry["reference_count"] = len(refs)
        (repaired if refs else still_broken).append(entry)

    return {"repaired": repaired, "still_broken": still_broken, "ok": not still_broken}


def survey_template_features(project, reference: dict) -> dict:
    """看工程里是不是**已经带全了**需要的特征 —— 用 `.fwp` 模板建工程时就会带全。

    判据（三样都要满足才算 `complete`）：
      1. **按名字**找齐 2 个边界条件 + 7 个目标 + 1 个局部网格
         （模板里的名字和人工工程里的一模一样）
      2. 每个特征的 `GetReferencesNames()` 非空
      3. 每个特征 `Rebuild(project)` 成功

    ⚠️ **「引用非空」只说明 Flow 认那几个名字，不说明几何对得上。**
    真正的几何判据在 S8/S11：
      * 出口静压必须精确等于 101325 Pa（边界条件）
      * **体积流量必须等于 `3 m/s × 封盖面积`**（实测 0.1027）—— 封盖面选错的话
        这个数立刻会变，它是**最灵敏的判别器**。
    """
    want = ([str(b["name"]) for b in reference["boundaries"]]
            + [str(g["name"]) for g in reference["goals"]]
            + [str((reference.get("local_mesh") or {}).get("name") or "")])
    want = [w for w in want if w]
    found: dict[str, dict] = {}
    enum = project.EnumFeatures()
    if enum is None:
        return {"complete": False, "reason": "EnumFeatures 返回空（多半有模态框卡着）",
                "expected": want, "found": {}}
    enum.Reset()
    for _ in range(2000):
        f = enum.Next()
        if f is None:
            break
        name = str(swa.safe_get(f, "Name", default="") or "")
        if name not in want:
            continue
        entry: dict = {}
        try:
            entry["type"] = int(f.Type)
        except Exception:  # noqa: BLE001
            pass
        try:
            topo = f.GetInterface("ITopologyBasedFeature")
            refs = [str(r) for r in (topo.GetReferencesNames() or [])] if topo is not None else []
            entry["references"] = refs
            entry["reference_count"] = len(refs)
        except Exception as exc:  # noqa: BLE001
            entry["references_error"] = f"{type(exc).__name__}: {exc}"
        try:
            entry["rebuild"] = bool(f.Rebuild(project))
        except Exception as exc:  # noqa: BLE001
            entry["rebuild"] = False
            entry["rebuild_error"] = f"{type(exc).__name__}: {exc}"
        found[name] = entry

    missing = [w for w in want if w not in found]
    empty = [n for n, e in found.items() if not e.get("reference_count")]
    unbuilt = [n for n, e in found.items() if not e.get("rebuild")]
    problems = []
    if missing:
        problems.append(f"缺特征 {missing}")
    if empty:
        problems.append(f"引用为空 {empty}")
    if unbuilt:
        problems.append(f"Rebuild 失败 {unbuilt}")
    return {"complete": not problems, "problems": problems,
            "expected": want, "found": found,
            "counts": {"expected": len(want), "found": len(found)}}


def add_goals(project, reference: dict, binding: dict, native, sw, *,
              allow_partial: bool,
              catalog: ComponentCatalog | None = None) -> tuple[list[dict], list[str]]:
    """写 2 个边界条件 + 尽可能多的目标。返回 (报告, 未解析/被跳过的目标名)。"""
    reports, unresolved = [], []
    face_cache: dict[str, tuple[list, list[dict]]] = {}

    for spec in reference["boundaries"]:
        candidates, face_name = resolve_goal_components(
            {**spec, "faces_on": [spec["faces_on"]]}, binding, native)
        logical = spec["role"]
        reports.append(fpj.add_boundary(
            project, spec, component_candidates=candidates, face_name=face_name,
            select=_cap_face_selector(sw, native, binding, logical,
                                      face_name or f"FLOW_{logical.upper()}_INNER",
                                      catalog=catalog)))

    for spec in reference["goals"]:
        candidates, face_name = resolve_goal_components(spec, binding, native)
        if candidates:
            logical = face_name[len("FLOW_"):-len("_INNER")].lower() if face_name else ""
            reports.append(fpj.add_surface_goal(
                project, spec, component_candidates=candidates, face_name=face_name,
                select=_cap_face_selector(sw, native, binding, logical, face_name or "",
                                          catalog=catalog)))
            continue

        # 密封面/蝶板面/阀轴面上的目标：这些面没有稳定名字，按**面序号**现场选。
        # 序号 = 人工工程 Faces_Keys 减去组件基址，来历见 config 的 face_id_cipher。
        selection = spec.get("face_selection") or []
        if not selection:
            unresolved.append(spec["name"])
            reports.append({"name": spec["name"], "skipped": True,
                            "reason": "配置里没有 face_selection"})
            if not allow_partial:
                raise NeedsSeatFaces(f"目标 {spec['name']!r} 没有面选择配置。"
                                     "用 --allow-partial-goals 可以先跳过它。")
            continue
        select = make_face_index_selector(native, selection, catalog=catalog,
                                          face_cache=face_cache)
        expected = spec.get("expect_faces") or sum(len(e["indices"]) for e in selection)
        try:
            reports.append(fpj.add_surface_goal(
                project, spec, select=select, expect_faces=expected,
                bind_component_too=[str(swa.safe_get(
                    catalog.component_by_token(t) if catalog is not None
                    else component_by_token(native, t), "Name2"))
                                    for t in (spec.get("components") or ())]))
        except Exception as exc:  # noqa: BLE001
            unresolved.append(spec["name"])
            reports.append({"name": spec["name"], "skipped": True, "selected_faces": expected,
                            "reason": f"{type(exc).__name__}: {exc}",
                            "picked": getattr(select, "last_picked", None),
                            "selector_diagnostic": getattr(select, "diagnostic", None)})
            if not allow_partial:
                raise NeedsSeatFaces(
                    f"目标 {spec['name']!r} 绑定失败（要 {expected} 个面）：{exc}。"
                    "用 --allow-partial-goals 可以先把这一行跑出来，缺的标签留空。") from exc

    return reports, unresolved


class NeedsSeatFaces(RuntimeError):
    """密封面尚未标定 —— 不是错误，是待办。"""


# ================================================================= 结果

def _trailing_window(n: int, *, floor: int = 50, fraction: float = 0.2) -> int:
    """时间平均的窗口长度：至少 `floor` 步、且不少于总步数的 `fraction`。

    实测（人工 level 2 的 `Goals.DAT`）：力矩Z 最后 53 步均值 −19.6449、
    最后 160 步均值 −19.6167 —— **差 0.03**。窗口取宽一点更稳，不必抠。
    """
    return max(1, min(n, max(floor, int(n * fraction))))


def read_goal_statistics(nca, project_dir: Path) -> tuple[dict[str, dict], dict]:
    """读 7 个目标的 **瞬时值 + 时间平均值 + 振荡幅度**。

    ⚠️ **为什么要平均值。** 稳态求解器碰上有非定常特征的流动时，瞬时值**永远收敛不了**。
    实测（蝶板 45°、Re≈6.3e5，蝶板后有涡脱落）：

    | 目标 | 最后 100 步振荡跨度 | 瞬时值位置 |
    |---|---|---|
    | `SG 力矩Z` | **6.49%** | 带里的随机一点 |
    | 密比压 平均/最大、蝶板法向压力 | 1.4~1.6% | 同上 |

    力矩Z 从 travel 2.5 起就在 −18.96~−19.98 之间来回摆，最后 100 步的线性斜率是
    −0.002/迭代（≈0）—— **是振荡，不是漂移，加 travel 也不会收敛**。
    `Progress` 跟着在 16~48 之间乱跳，就是这个原因。

    官方 API 的 `GetValues()` 给的是**逐迭代序列**，所以平均值不用去解析 `Goals.DAT` 文本。
    每次会把序列的最后一个与 `GetLastCalculatedValue()` 核对 —— 对不上说明 API 语义变了，
    宁可炸也别静默用错列。

    返回 `(每个目标的统计, 元信息)`。
    """
    project_dir = Path(project_dir)
    flds = [p for p in project_dir.glob("*.fld") if not p.name.startswith("r_")]
    if not flds:
        raise RuntimeError(f"{project_dir} 里没有 .fld")
    fld = max(flds, key=lambda p: p.stat().st_mtime)
    handler = nca.LoadFDAResultFile(str(fld), True)
    if handler is None:
        raise RuntimeError(f"LoadFDAResultFile({fld}) 返回空")
    enum = handler.GetGoalsCalculationResults2().GetGoalsEnum()
    enum.Reset()
    stats: dict[str, dict] = {}
    problems: list[str] = []
    for _ in range(1000):
        goal = enum.Next()
        if goal is None:
            break
        name = str(goal.GetGoalName())
        if name not in fg.GOAL_COLUMNS:
            continue
        entry: dict[str, Any] = {"value": float(goal.GetLastCalculatedValue())}
        try:
            series = [float(v) for v in (goal.GetValues() or [])]
        except Exception as exc:  # noqa: BLE001
            series = []
            entry["series_error"] = f"{type(exc).__name__}: {exc}"
        entry["n_iterations"] = len(series)
        if series:
            if abs(series[-1] - entry["value"]) > max(abs(entry["value"]) * 1e-6, 1e-9):
                problems.append(
                    f"{name}: 序列末值 {series[-1]} 与 GetLastCalculatedValue {entry['value']} 不符")
            w = _trailing_window(len(series))
            tail = series[-w:]
            mean = sum(tail) / w
            entry.update({
                "window": w,
                "av_value": mean,
                "window_min": min(tail),
                "window_max": max(tail),
                "spread_abs": max(tail) - min(tail),
                "spread_rel": (max(tail) - min(tail)) / abs(mean) if mean else None,
            })
        stats[name] = entry
    if not stats:
        raise RuntimeError("一个目标都没读到")
    meta = {"fld": str(fld), "problems": problems,
            "note": "value=瞬时(最后一迭代)，av_value=最后 window 步的时间平均"}
    return stats, meta


def read_goals_from_goals_dat(project_dir: Path) -> dict[str, dict]:
    """从 `Goals.DAT/<目标名>.txt` 读 **Flow 自己算的** 数值/平均值/最小值/最大值/增量/标准/进度。

    ⚠️ **为什么必须用这个文件，而不能用 API 自己算。** Flow 的「平均值」在公开 API 里
    **读不到**（2026-09-21 实测，见 `scripts/Probe-GoalValues.py`）：

        GetValues()             → 320 个**瞬时值**
        GetValues2()            → **和上面逐位相同**（同样是 320 个瞬时值）
        GetLastCalculatedValue()→ 序列最后一个，还是瞬时值
        平均值 / 最小值 / 最大值 → **API 里根本没有**

    这三个数只写在 `Goals.DAT` 这个纯文本文件里。实测该文件的列与 GUI「目标图」的列
    **逐位相同**（同一行 `Value=213169.234 AvValue=213911.637 MinValue=213169.234
    MaxValue=214405.497 Delta=447.64865 Criteria=1065.84617` 对上界面上的
    数值/平均值/最小值/最大值/增量/标准）。

    「自己按尾窗算平均」和它差 **0.17~0.68%**（实测），所以**用户界面上看到的数和
    训练行里的数会不一致** —— 那是不能接受的，必须读 Flow 的原值。

    返回 `{目标名: {...}}`，键名与 `read_goal_statistics` 对齐（`value` / `av_value`）。
    """
    project_dir = Path(project_dir)
    gd = project_dir / "Goals.DAT"
    if not gd.is_dir():
        return {}
    out: dict[str, dict] = {}
    for f in sorted(gd.glob("*.txt")):
        if f.stem.startswith("Serv") or f.stem == "global_parameters" or f.stem not in fg.GOAL_COLUMNS:
            continue
        lines = [l for l in f.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        header = lines[0].split("\t")
        last = lines[-1].split("\t")
        row = dict(zip(header, last))

        def num(key):
            try:
                return float(row[key])
            except (KeyError, ValueError):
                return None

        av, mn, mx = num("AvValue"), num("MinValue"), num("MaxValue")
        entry = {
            "source": "Goals.DAT",
            "value": num("Value"),
            "av_value": av,
            "window_min": mn,
            "window_max": mx,
            "spread_abs": (mx - mn) if (mx is not None and mn is not None) else None,
            "spread_rel": ((mx - mn) / abs(av)) if (mx is not None and mn is not None and av) else None,
            "delta": num("Delta"),
            "criteria": num("Criteria"),
            "progress": num("Progress"),
            "criteria_percentage": num("CriteriaPercentage"),
            "criteria_type": num("CriteriaType"),
            "n_iterations": int(num("Iteration") or 0) or None,
        }
        out[f.stem] = entry
    return out


def goal_values(stats: dict[str, dict], mode: str = "time_averaged") -> dict[str, float]:
    """从统计里取训练行用的那一列。缺 `av_value` 时退回瞬时值并留痕。"""
    out: dict[str, float] = {}
    for name, entry in stats.items():
        if mode == "instantaneous":
            out[name] = entry["value"]
        else:
            out[name] = entry.get("av_value", entry["value"])
    return out


def read_goals(nca, project_dir: Path, *, mode: str = "time_averaged") -> dict:
    """兼容旧签名（`Run-FlowSingle.py` 在用），内部走 `read_goal_statistics`。"""
    stats, _ = read_goal_statistics(nca, project_dir)
    return goal_values(stats, mode)


def physics_gate(goals: dict, reference: dict) -> dict:
    """免费物理闸门：出口静压就是边界条件，必须精确；压差必须为正。"""
    ref = reference["acceptance_gates"]
    problems = []
    p_out = goals.get("SG CV出口静压")
    if p_out is None:
        problems.append("缺 SG CV出口静压")
    elif abs(p_out - ref["outlet_static_pressure_exact_pa"]) > ref["outlet_static_pressure_tolerance_pa"]:
        problems.append(f"出口静压 {p_out} 与边界条件 {ref['outlet_static_pressure_exact_pa']} 不符")
    q = goals.get("SG CV入口端面体积流量")
    if q is not None and not q > 0:
        problems.append(f"体积流量 {q} 不为正")
    dp = (goals.get("SG CV入口静压") or 0.0) - (p_out or 0.0)
    if dp <= 0:
        problems.append(f"压差 {dp} 不为正")
    return {"problems": problems, "ok": not problems, "delta_p_pa": dp}


def write_training_row(row: dict, run: Path, dataset_xlsx: Path) -> dict:
    """追加一行。Excel 占着文件时降级为「只写 run 本地副本」，**不算失败**。"""
    local = Path(run) / "training_sample.csv"
    with open(local, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fg.TRAINING_COLUMNS))
        writer.writeheader()
        writer.writerow(row)
    result = {"run_csv": str(local)}
    try:
        write_result = _append_xlsx(dataset_xlsx, row)
        result.update({"dataset_xlsx": str(dataset_xlsx), **write_result})
    except Exception as exc:  # noqa: BLE001
        result["appended"] = False
        result["note"] = f"主表没写进去（不算失败，run 本地副本已写）：{type(exc).__name__}: {exc}"
    return result


def _rows_match(left, right, columns) -> bool:
    """训练表数值比较：兼容 Excel 回读后的 int/float 形态，不吞掉真实差异。"""
    for column in columns:
        a, b = left.get(column), right.get(column)
        if a is None or b is None:
            if a != b:
                return False
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if not math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12):
                return False
        elif a != b:
            return False
    return True


def _append_xlsx(path: Path, row: dict) -> dict:
    """按七变量设计点幂等追加；同输入不同标签时拒绝静默污染数据集。"""
    from openpyxl import Workbook, load_workbook
    path = Path(path)
    if path.is_file():
        wb = load_workbook(path)
        ws = wb.active
        header = [c.value for c in ws[1]]
        if header != list(fg.TRAINING_COLUMNS):
            raise RuntimeError(
                f"{path} 的表头与 {len(fg.TRAINING_COLUMNS)} 列契约不符，拒写。"
                "（契约刚加过列时最容易撞上这条 —— 老表要先补上新表头再跑）")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "训练数据"
        ws.append(list(fg.TRAINING_COLUMNS))
    try:
        input_columns = tuple(fg.DESIGN_COLUMNS)
        #: ⚠️ 「是不是同一行」的判据必须**排除样本序号** —— 编号是元数据，不是内容。
        #: 把它算进去的话，一条老行（编号为空）和一条新行（编号=7）即便七变量与
        #: 目标值逐位相同，也会被判成「同输入不同目标」而拒写整批数据。
        content_columns = tuple(fg.DESIGN_COLUMNS + fg.GOAL_COLUMNS + fg.LABEL_COLUMNS)
        all_columns = tuple(fg.TRAINING_COLUMNS)
        for values in ws.iter_rows(min_row=2, values_only=True):
            existing = dict(zip(all_columns, values))
            if not _rows_match(existing, row, input_columns):
                continue
            if _rows_match(existing, row, content_columns):
                return {"appended": False, "deduplicated": True,
                        "note": "相同七变量与目标数据已存在，未重复追加"}
            raise RuntimeError(
                "主表已存在相同七变量但目标值不同的行，拒绝静默覆盖或重复追加；"
                "请先核查对应 run 的物理结果")
        ws.append([row[c] for c in all_columns])
        tmp = path.with_suffix(".tmp.xlsx")
        wb.save(tmp)
        tmp.replace(path)
        return {"appended": True, "deduplicated": False}
    finally:
        wb.close()


# ================================================================= 主流程

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="单个样本：几何已验收 → 训练行")
    p.add_argument("run_name")
    p.add_argument("--execute", action="store_true", help="真的执行（默认只预检）")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--dump-faces", action="store_true",
                   help="只读：导出装配体全部面到 <run>/faces_dump.json，然后退出")
    p.add_argument("--design", default="",
                   help="七变量设计点 JSON。Run-OneDesign.exe 写的 e2e 报告里没有设计点"
                        "（嵌套对象被序列化成类型名），所以走那条链时必须给")
    p.add_argument("--config", default="开度45°", help="配置名（开度扫掠时按开度传）")
    p.add_argument("--fwp", default=str(DEFAULT_FWP))
    p.add_argument("--mesh-only", action="store_true", help="走到网格就停，不求解")
    p.add_argument("--yes-solve", action="store_true", help="确认求解（30~60 分钟不可中断）")
    p.add_argument("--allow-partial-goals", action="store_true",
                   help="密封面未标定时也继续：缺的标签留空（契约允许，禁止补零）")
    p.add_argument("--timeout-min", type=float, default=120.0)
    p.add_argument("--rho-kg-m3", type=float, default=998.2)
    p.add_argument("--dataset-xlsx", default=str(TRAINING_XLSX))
    p.add_argument("--sample-id", default=None,
                   help="样本编号（Variables/*.xlsx 的第一列）。批量跑时由 Run-Batch.py 传入；"
                        "单样本跑不给，训练表该列留空")
    p.add_argument("--ray-overrides", default="", help='JSON：{"upper_body": 0.3}')
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # --dump-faces 是只读的（开文档、读面、退出），不该要求 --execute
    if args.dump_faces:
        args.execute = True
    run = (RUNS_ROOT / args.run_name).resolve()
    report: dict[str, Any] = {"schema_version": 1, "run": str(run), "started_utc": now(),
                              "mode": "execute" if args.execute else "preflight",
                              "config_name": args.config, "sample_id": args.sample_id,
                              "stages": {}}
    out_path = run / ("flow_sample.json" if args.execute else "flow_sample_preflight.json")
    session = None
    try:
        if RUNS_ROOT != run and RUNS_ROOT not in run.parents:
            raise RuntimeError(f"run 目录必须落在 {RUNS_ROOT} 之下：{run}")
        if not run.is_dir():
            raise RuntimeError(f"run 目录不存在：{run}")

        mapping_path, mapping = find_mapping_report(run)
        assert_geometry_verified(mapping, mapping_path)
        report["mapping_report"] = str(mapping_path)
        report["design"] = load_design(run, mapping,
                                       Path(args.design) if args.design else None)
        assembly = assembly_of(run)
        report["assembly"] = str(assembly)

        state = load_state(run)
        if args.resume:
            ok, why = resume_is_safe(run, state)
            report["resume_check"] = {"ok": ok, "why": why}
            if not ok:
                raise RuntimeError(f"拒绝续跑：{why}")
        elif args.execute and not args.dump_faces and state.get("stages"):
            # --dump-faces 是只读的，随时可以重跑；写操作才需要 --resume 把关
            raise RuntimeError("这条 run 已有进度记录 —— 接着跑请加 --resume；重来请删掉 run 目录重克隆")

        session = fses.SwSession.attach()
        report["stages"]["gate"] = {**session.gate_or_raise(run=run), "ok": True}

        if not args.execute:
            report["status"] = "preflight_ready"
            report["next"] = "加 --execute（--dump-faces 可先只读导出面数据）"
            return _finish(report, out_path, 0)

        # ---- S1 开装配体
        document = fpj.open_assembly(session, assembly, args.config)
        configuration = fpj.active_configuration(document, args.config)
        # ⚠️ `keep_under=run`：**本 run 自己的工程要留下** —— 上一次停在求解前时
        # 已经把它存进装配体了，这一次本该直接激活。不加这个参数会把我们自己的
        # 当成"模板残留"删掉，S6 就只能重建（白花 ~20 秒 + 重写 9 个特征）。
        project_gate = session.assert_no_flow_project(configuration, remove=True, keep_under=run)
        report["stages"]["open_assembly"] = {"configuration": args.config,
                                             "flow_project_gate": project_gate, "ok": True}
        _record(state, run, "open_assembly")

        # ---- 只读导出（用于补完密封面识别 + 核对开口推导）
        if args.dump_faces:
            native_doc = session.sw.ActiveDoc
            faces = enumerate_faces(native_doc)
            edges = enumerate_open_circular_edges(native_doc)
            dump = run / "faces_dump.json"
            dump.write_text(json.dumps(
                {"run": str(run), "configuration": args.config,
                 "faces": faces, "circular_edges": edges},
                ensure_ascii=False, indent=2), encoding="utf-8")
            report["status"] = "faces_dumped"
            report["faces_dump"] = {
                "path": str(dump),
                "face_counts": {k: len(v) for k, v in faces.items()},
                "circular_edge_count": len(edges),
                "components": sorted(faces),
            }
            # 顺手用真实数据核对开口推导 —— 只读，失败不影响导出
            try:
                derived = fg.openings_from_edges(edges)
                report["faces_dump"]["derived_openings"] = derived
            except Exception as exc:  # noqa: BLE001
                report["faces_dump"]["opening_derivation_error"] = f"{type(exc).__name__}: {exc}"
            return _finish(report, out_path, 0)

        # ---- S2 CAD 重建（此刻绝不碰任何 Flow 特征）
        native = session.sw.ActiveDoc
        rebuilt = bool(native.ForceRebuild3(False))
        if not rebuilt:
            raise RuntimeError("ForceRebuild3 返回 False")
        hashes = geometry_hashes(run)
        report["stages"]["cad_rebuild"] = {"force_rebuild": True, "hashes": hashes, "ok": True}
        _record(state, run, "cad_rebuild", {"hashes": hashes})

        # ---- S3 开口识别（只读，先算出来）
        # ⚠️ **只在真要建封盖时才算。** 这个结果的唯一消费者是下面 S4 的 Create Lids
        # （见那行 lambda），而 `lids` 已完成时 S4 直接跳过 —— 于是续跑/求解那一次会
        # 白枚举一遍全部圆边。实测这一步 25.7 / 39.1 / 48.1 秒（三次单调上升），
        # 比整场求解（~48 秒）还贵，占了求解调用的三分之一。
        need_lids = not _done(state, "lids")
        openings = None
        if need_lids:
            edges = enumerate_open_circular_edges(native)
            # 起点覆盖优先取命令行，其次取冻结配置里的标定值（见 flow_physics_reference.json）
            overrides = json.loads(args.ray_overrides) if args.ray_overrides else \
                {k: v for k, v in (fpj.load_reference().get("ray_start_overrides") or {}).items()
                 if not k.startswith("_") and not k.endswith("_reason")} or None
            openings = fg.openings_from_edges(edges, ray_start_overrides=overrides)
            report["stages"]["openings"] = {"edge_count": len(edges), "openings": openings, "ok": True}
            _record(state, run, "openings")
        else:
            report["stages"]["openings"] = {**state["stages"].get("openings", {}),
                                            "skipped": "封盖已建，S4 不会再跑，本阶段无消费者"}

        # ---- S4 Create Lids（不幂等：跳过已完成的）
        # ⚠️ 判据必须和 S3 那个 `need_lids` **同源** —— 两处各判一次的话，
        # S3 算了而 S4 跳过（或反过来拿到 None）就会在运行期炸掉。
        if not need_lids:
            report["stages"]["lids"] = {**state["stages"]["lids"], "skipped": "已完成"}
        else:
            report["stages"]["lids"] = fses.guarded(
                lambda: do_create_lids(session.sw, run, args.config, openings),
                session, step="Create Lids")
            _record(state, run, "lids", {"hashes": geometry_hashes(run)})

        # ---- S5 封盖↔开口关联 + 命名
        # 只有【带配对明细】的记录才敢跳过 —— 明细缺失就重跑（重新命名同一批面是幂等的），
        # 否则 S7 解析封盖目标时会 KeyError: 'paired'
        if _done(state, "cap_binding") and state["stages"]["cap_binding"].get("paired"):
            report["stages"]["cap_binding"] = {**state["stages"]["cap_binding"], "skipped": "已完成"}
        else:
            binding = fses.guarded(
                lambda: do_bind_caps(session.sw, session.sw.ActiveDoc, run, args.config),
                session, step="封盖关联与命名")
            report["stages"]["cap_binding"] = binding
            # 配对明细必须进状态文件 —— 续跑时 S7 要靠它解析封盖上的目标，
            # 只记 {ok, at_utc} 的话续跑会 KeyError: 'paired'（实测踩过）
            _record(state, run, "cap_binding",
                    {"paired": binding["paired"], "named": binding["named"],
                     "hashes": geometry_hashes(run)})

        # ---- S6 新建 Flow 工程（不幂等：已完成就只激活，不重建）
        #
        # ⚠️ 续跑时可能**激活不到已记录的工程**：按设计「S7 通过之前不保存装配体」，
        # 所以上一次尝试中断时工程只存在内存里，关掉文档就没了（封盖是保存过的，所以还在）。
        # 这种情况不是"现场和状态文件不符"，而是**预料之中的** —— 重新建一次即可，
        # 但必须同时把已记录的 S7 特征作废（新工程里没有它们）。
        project = None
        activate_problem = None
        if _done(state, "project"):
            try:
                project = configuration.ActivateProject(PROJECT_NAME, False)
            except Exception as exc:  # noqa: BLE001
                # ⚠️ 原来这里是裸 `except: project = None` —— 失败原因被吞掉，
                # 表现为"莫名其妙又重建了一次工程"，查半天。记进报告。
                activate_problem = f"{type(exc).__name__}: {exc}"
                project = None
            if project is None and activate_problem is None:
                activate_problem = "ActivateProject 返回 None（装配体里没有这个工程）"
            if project is not None:
                project_dir = Path(str(getattr(project.ProjectFiles, "ProjectDirectory", ""))).resolve()
                # 激活到的必须是**本 run 的**工程。指向别处 = 继承来的注册，
                # 用它会让 S8 去读别人的 xmlconfig —— 宁可重建。
                if run.resolve() not in project_dir.parents:
                    activate_problem = f"激活到的工程不在本 run 内：{project_dir}"
                    project = None
                else:
                    report["stages"]["project"] = {"activated_existing": True,
                                                   "project_directory": str(project_dir), "ok": True}
        if project is None:
            stale = sorted(set(state.get("stages", {})) & {"project", "features"})
            for stage in stale:
                state["stages"].pop(stage, None)
            if stale:
                save_state(run, state)
            report["project_recreated_because"] = (
                "装配体里没有可激活的工程（多半是上一次中断时还没保存装配体）—— "
                f"已作废 {stale}，重新建工程")
            if activate_problem:
                report["project_activate_problem"] = activate_problem
            report["purged_project_dir"] = purge_project_dir(run)
            built = fpj.create_project(document, configuration, Path(args.fwp), PROJECT_NAME)
            project, project_dir = built["project"], Path(built["report"]["project_directory"])
            report["stages"]["project"] = built["report"]
            _record(state, run, "project")

        reference = fpj.load_reference()
        component_catalog = ComponentCatalog(native)

        # ---- S7 特征：模板带了就用模板的，没带才自己写
        # ⚠️ 2026-09-21：用户把人工工程**存成了 .fwp 模板**（`开度45°.fwp`），
        # 建出来的工程和人工的 `1.xmlconfig` **逐字段相同**（11 个原先不同的字段全对上了，
        # 包括 `CellPerGap=12`、判据 0.5%、`RefLevel=2` —— 这三个公开 API 根本写不了）。
        # 所以现在的首选是「用模板 + 只校验」，自己写只是退路。
        survey = survey_template_features(project, reference)
        #: 空模板（`internal_water.fwp`）什么都不带 → `found` 为空 → 走"自己写"那条路。
        #: 带特征的模板（用户的 `开度45°.fwp`）→ `found` 非空 → 走"校验 + 修复"。
        template_brought_features = bool(survey.get("found"))
        if survey["complete"]:
            report["stages"]["features"] = {"source": "template", **survey, "ok": True}
            _record(state, run, "features")
        elif template_brought_features:
            # 模板把特征带过来了，但**挂在封盖上的那几个引用是断的** ——
            # 这是预期内的（我们的封盖是 TB_CloseHole 造的）。重绑它们，
            # **不要**走"自己重建一遍"那条路：那条路会因为名字已存在而失败
            # （实测：`AddTemporaryFeature('入口速度 2') 返回 False`）。
            repair = repair_template_features(
                project, reference, report["stages"]["cap_binding"], native, session.sw, survey,
                catalog=component_catalog)
            report["stages"]["features"] = {"source": "template+repaired", "survey": survey,
                                            "repair": repair, "ok": repair["ok"]}
            if not repair["ok"]:
                raise RuntimeError(
                    "模板带过来的封盖类特征重绑失败：" +
                    json.dumps(repair["still_broken"], ensure_ascii=False))
            _record(state, run, "features")
        elif _done(state, "features"):
            report["stages"]["features"] = {**state["stages"]["features"], "skipped": "已完成"}
        else:
            # ⚠️ 先把 survey 落进报告再动手 —— 否则回退路径一失败，就看不到
            # "为什么判它不完整"了（实测踩过：报 `AddTemporaryFeature 返回 False`，
            # 而真因是特征名已存在、survey 漏判）。
            report["stages"]["features"] = {"source": "api_written", "template_survey": survey,
                                            "ok": False}
            features, unresolved = add_goals(
                project, reference, report["stages"]["cap_binding"], native, session.sw,
                allow_partial=args.allow_partial_goals, catalog=component_catalog)
            # 局部网格必须在特征之后建 —— 没有它网格会粗 4.6 倍（实测 3892 vs 18048）
            local_mesh = add_local_mesh_feature(native, project, reference,
                                                catalog=component_catalog)
            report["stages"]["features"] = {"source": "api_written", "features": features,
                                            "unresolved_goals": unresolved,
                                            "local_mesh": local_mesh,
                                            "template_survey": survey, "ok": True}
            _record(state, run, "features")

        # ---- S8 内部域门禁（必须在 UpdateConfigAndDataFiles 之后读）
        project.UpdateConfigAndDataFiles()
        report["stages"]["internal_gate"] = fpj.internal_flow_gate(project_dir)
        _record(state, run, "internal_gate")

        # ---- S9 网格 + 收敛（幂等，重跑无害）
        report["stages"]["mesh"] = fpj.apply_mesh_and_control(project, reference)
        _record(state, run, "mesh")

        # ---- S9b 目标收敛判据 → 0.5%
        # ⚠️ 公开 API 根本没有收敛判据的属性（目标特征的 EnumParameters() 返回空，
        # 人工工程里也是空的），只能改序列化文件。这里先写一次**为了看得见**
        # （停在求解前那一步时 report 里就有），求解前 `document.Save()` 之后**再写一次**
        # —— 那才是真正生效的那一次（Save()/Flow 自己都可能把它覆盖回去）。
        report["stages"]["goal_criteria"] = fpj.apply_goal_criteria_percentage(project_dir, reference)
        _record(state, run, "goal_criteria")

        # ---- 把工程和几何落盘
        # ⚠️ 少了这一步，Flow 工程只活在**内存**里 —— 关掉文档就没了，下一次 `--resume`
        # 只能重建工程（实测白花 ~20 秒：重建工程 + 重写 9 个特征 + 局部网格）。
        # 计划里「S7 门禁通过之前绝不保存装配体」到这里已经满足（S7/S8/S9 三道门禁全过），
        # 所以此刻保存是安全的。位置和原来求解路径那一次保存相同 —— 都在 `Solve2` 之前，
        # 只是提前到分支之前，这样 `--mesh-only` 和「停在求解前」两条路也能落盘。
        document.Save()
        persisted = geometry_hashes(run)
        report["stages"]["persist"] = {"saved": True, "hashes": persisted, "ok": True}
        # 必须记哈希：保存改写了全部零件/装配体文件，不记的话下次续跑会被守卫拒掉
        _record(state, run, "persist", {"hashes": persisted})
        # `Save()` 之后 Flow 可能把判据覆盖回去 —— 和求解路径一样，再写一次。
        report["stages"]["goal_criteria_after_save"] = fpj.apply_goal_criteria_percentage(
            project_dir, reference)

        if args.mesh_only:
            report["status"] = "mesh_configured_only"
            return _finish(report, out_path, 0)
        if not args.yes_solve:
            report["status"] = "awaiting_solve_confirmation"
            report["note"] = "已到求解前一步。确认后加 --yes-solve（30~60 分钟不可中断）。"
            return _finish(report, out_path, 0)

        # ---- S10 求解
        # 几何已经在上面 persist 那一段落盘了（Flow 才会把结果绑到正确的几何上），
        # 那一次的哈希也记进了 `persist` 阶段 —— 所以这里不再重复 Save()。
        # ⚠️ 求解本身也会**改动全部零件/装配体文件**，所以 solve 同样是"改动几何的阶段"，
        # 它的哈希在 wait_for_result 之后才记（见下），不然下一次 --resume 会被守卫拒掉。
        # 求解前**再写一次**判据：这是最后一次写盘机会。
        # 真有没有生效由 S11 读 `1.info.json` 反查（Flow 可能用内存状态把它覆盖回去）。
        report["stages"]["goal_criteria_before_solve"] = fpj.apply_goal_criteria_percentage(
            project_dir, reference)

        started = time.time()
        report["stages"]["solve"] = {"solve2": bool(project.Solve2(True, True, True, False)),
                                     "started_epoch": started,
                                     "pre_solve_hashes": geometry_hashes(run), "ok": True}
        _record(state, run, "solve")   # 先只留"求解开始过"的痕迹，哈希等求解结束再记

        fpj.wait_for_result(project_dir, started_epoch=started, timeout_s=args.timeout_min * 60.0,
                            on_poll=lambda info: report.__setitem__("solver_monitor", info))

        # ⚠️ **几何哈希必须在求解结束之后取**。`document.Save()` 那一步之后装配体是"要算的
        # 那份"，但**求解本身会把结果写回装配体**（Flow 把工程/结果存进文档），磁盘状态又变了。
        # 记求解前的哈希 → 下一次 `--resume` 必然被自己的守卫拒掉
        # （实测：`solve 之后记录的几何与磁盘不符 —— 变了 ['...组装图.SLDASM']`，
        #  于是求解完了反而读不了结果、也重算不了）。
        solve_hashes = geometry_hashes(run)
        report["stages"]["solve"]["hashes"] = solve_hashes
        _record(state, run, "solve", {"hashes": solve_hashes})

        # ---- S11 读结果
        # ⚠️ 取**时间平均**而不是瞬时值：稳态求解器碰上有涡脱落的流动时，
        # 瞬时值永远收敛不了（实测 SG 力矩Z 在 6.5% 的带子里乱跳，Progress 16~48）。
        value_mode = reference.get("goals_value_mode", "time_averaged")
        # 主源：`Goals.DAT`（**Flow 自己算的平均值**，与 GUI 逐位相同）
        goal_stats = read_goals_from_goals_dat(project_dir)
        goal_meta = {"source": "Goals.DAT"}
        if goal_stats:
            goals = goal_values(goal_stats, value_mode)
        else:
            # 退路：自己按尾窗算。只在 Goals.DAT 缺失时走 —— 注意它和 Flow 的值差
            # 0.17~0.68%，所以这算**降级**，报告里必须留痕。
            goal_stats, goal_meta = read_goal_statistics(session.api, project_dir)
            goal_meta["source"] = "api_trailing_window"
            goal_meta["degraded"] = "Goals.DAT 缺失 —— 平均值是自算的尾窗均值，与 GUI 不一致"
            goals = goal_values(goal_stats, value_mode)
        criteria = fpj.verify_goal_criteria(project_dir, reference, list(fg.GOAL_COLUMNS))
        face_counts = fpj.solver_goal_face_counts(project_dir)
        human_counts = reference.get("human_solver_face_counts") or {}
        # 目标面数只当**旁证**：它是网格面数，受局部网格影响，不能当判据；
        # 但它对"选错面"极敏感（实测选锥面时密比压 841 vs 人工 288）。
        # ⚠️ 配置里还有个 `ours_when_wrong`（记录上次选错时的值），要排掉。
        skip = {k for k in human_counts if not isinstance(human_counts[k], int)}
        face_comparison = {name: {"ours": face_counts.get(name), "human": human_counts.get(name)}
                           for name in sorted(set(face_counts) | set(human_counts))
                           if not name.startswith("_") and name not in skip}
        # 停机点：人工是**撞 travel 上限**（8.00589），不是所有目标收敛。
        # 判据不生效 / UseManualMaximumTravels 没落盘时，这里会明显偏小（实测踩过 4.003）。
        telemetry = (fpj.read_info_json(project_dir) or {}).get("telemetry") or {}
        travel = telemetry.get("travel")
        report["stages"]["results"] = {"goals": goals, "goals_value_mode": value_mode,
                                       "goal_statistics": goal_stats, "goal_read_meta": goal_meta,
                                       "criteria_verification": criteria,
                                       "solver_goal_face_counts": face_counts,
                                       "goal_face_counts_vs_human": face_comparison,
                                       "solver_telemetry": telemetry,
                                       "stop_point_vs_human": {
                                           "travel_ours": travel,
                                           "travel_human": reference["human_truth"]["travels"],
                                           "iterations_ours": telemetry.get("iteration"),
                                           "iterations_human": reference["human_truth"]["iterations"],
                                       },
                                       **physics_gate(goals, reference), "ok": True}
        # 振荡幅度超参考的，单独记一条 —— 不改判否（力矩Z 天生就大），但要能看见
        warn_rel = (reference.get("goal_oscillation_reference") or {}).get("warn_spread_rel", 0.02)
        oscillating = {n: round(e["spread_rel"], 4) for n, e in goal_stats.items()
                       if e.get("spread_rel") and e["spread_rel"] > warn_rel}
        report["stages"]["results"]["goals_oscillating"] = oscillating
        if oscillating:
            report.setdefault("notes", []).append(
                f"这些目标在最后窗口里振荡超过 {warn_rel:.0%}（已按时间平均报值）：{oscillating}")
        if travel is not None and float(travel) < reference["human_truth"]["travels"] - 0.5:
            report.setdefault("warnings", []).append(
                f"停机点 travel={travel}，人工 {reference['human_truth']['travels']:.3f} —— "
                "收敛控制没完全落盘（多半是 UseManualMaximumTravels 没生效）")
        if not report["stages"]["results"]["ok"]:
            raise RuntimeError("物理闸门不过：" + "; ".join(report["stages"]["results"]["problems"]))
        if not criteria["ok"]:
            # **已知且预期**：Flow 会在 Solve2 时用内存状态重写 xmlconfig，判据改不进去。
            # 停机由 `use_goals_convergence=False` + `MaxDV=8` 保证（撞 travel 上限），
            # 和人工那次的实际行为一致。所以这里记 NOTE 不记 warning —— 但必须留痕，
            # 将来 Flow 版本变了、判据真能生效时，这一条会自己消失，那就是升级信号。
            report.setdefault("notes", []).append(
                "目标收敛判据没能写成 0.5%（已知：Solve2 会用内存状态重写 xmlconfig）。"
                "停机改用 use_goals_convergence=False + MaxDV=8。明细："
                + "; ".join(criteria["problems"]))
        _record(state, run, "results")

        # ---- S12 训练行
        row = fg.build_training_row(report["design"], goals, args.rho_kg_m3,
                                    sample_id=args.sample_id)
        problems = fg.training_row_matches_contract(row)
        if problems:
            raise RuntimeError("训练行不满足契约：" + "; ".join(problems))
        report["stages"]["training_row"] = {"row": row, "ok": True}
        report["training_row_written"] = write_training_row(row, run, Path(args.dataset_xlsx))
        _record(state, run, "training_row")

        report["status"] = "completed"
        return _finish(report, out_path, 0)

    except fses.ModalDialogBlocked as exc:
        report.update({"status": "blocked_by_modal", "error": str(exc)})
        return _finish(report, out_path, 3)
    except NeedsSeatFaces as exc:
        report.update({"status": "needs_seat_faces", "error": str(exc)})
        return _finish(report, out_path, 4)
    except Exception as exc:  # noqa: BLE001
        report.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc()})
        return _finish(report, out_path, 1)
    finally:
        if session is not None:
            try:
                session.close_all_documents()
            finally:
                session.unload()


def _finish(report: dict, out_path: Path, code: int) -> int:
    report["finished_utc"] = now()
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps({"status": report.get("status"), "report": str(out_path)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
