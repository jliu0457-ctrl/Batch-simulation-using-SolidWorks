#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯几何分类：开口识别、封盖↔开口配对、密封面识别、训练行构造。

设计约束（必须保持）：
  * 本模块**不导入 pywin32 / flow_transfer / 任何 COM**。
    它只吃「从 COM dump 出来的 plain dict」，所以能离线 pytest。
  * `valve_mapping/` 保持纯标准库，所以将来这个模块若要被它引用也不会破坏规则。

为什么值得单独拆出来：这是整条链上唯一**可以离线验证**的部分。
靶子是两份已经存在的真值文件：
  working/flow_lid_reference_faces.json            —— 四个封盖的面（模板还有原生封盖时抓的）
  working/seven_variable_trials/flow_lids_013/open_circular_edges.json —— 448 条圆边
见 tests/test_flow_geometry.py。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

LOGICAL_OPENINGS = ("inlet", "outlet", "upper_body", "lower_body")

#: 组件基名（去掉 "^装配体-1" 后缀之后）到逻辑开口的映射。
PIPE_COMPONENT = {"inlet": "入口管道", "outlet": "出口管道"}
BODY_COMPONENT = "03阀体"

#: 射线半径，与 Create-FlowLids.py 实测一致。
RAY_RADIUS_M = 0.001
#: SelectByRay 的实体类型：FACE。
SELECT_TYPE_FACE = 2

#: 管口射线起点相对管口外端面向外的余量（米）。只要保证起点在实体之外即可。
PIPE_RAY_MARGIN_M = 0.1

#: 内/外面判别规则（plan §1.6）。法向符号不可靠，只能用坐标。
INNER_FACE_RULE = {
    "inlet": ("max", 0),       # 较大的 X
    "outlet": ("min", 0),      # 较小的 X
    "upper_body": ("min", 2),  # 较小的 Z
    "lower_body": ("max", 2),  # 较大的 Z
}

AXIS_NAME = {0: "X", 1: "Y", 2: "Z"}

#: 基准七变量设计下四个封盖【内面】的位置（米）。来源
#: `working/flow_lid_reference_faces.json`（模板还有原生封盖时抓的），人工核对过。
#: ⚠️ 这是**基准几何的参照**，用来核对封盖有没有盖对位置；不是跨设计的不变常数。
REFERENCE_INNER_PLANE = {
    "inlet": -0.6052332694371678,
    "outlet": 1.105233251722155,
    "upper_body": 0.10123323400713326,
    "lower_body": -0.23773323400713325,
}
#: 对应的内面面积（平方米）。
REFERENCE_INNER_AREA = {
    "inlet": 0.03660342203376061,
    "outlet": 0.036603422033760616,
    "upper_body": 0.0009368124082605323,
    "lower_body": 0.003506824466153257,
}


# ---------------------------------------------------------------- 小工具

def base_component(name: str) -> str:
    """'入口管道^8“D94R3Y-CL600C-00组装图-1' -> '入口管道'。

    SolidWorks 的组件名是「基名^装配体名-序号」，虚拟零件（管道、焊缝）和内嵌零件
    都会带这个后缀。判语义只用基名，不能用全名。
    """
    return str(name).split("^")[0]


def component_with_instance(name: str) -> str:
    """取 'X^assem-1' 的 'X-1' 形式 —— Flow 的引用串用的是这一种。"""
    parts = str(name).split("^")
    if len(parts) == 2 and "-" in parts[1]:
        return f"{parts[0]}-{parts[1].rsplit('-', 1)[-1]}"
    return str(name)


def _round(v: float, n: int = 6) -> float:
    return round(float(v), n)


def _almost_unit(vec: Sequence[float]) -> Sequence[float]:
    n = math.sqrt(sum(float(x) * float(x) for x in vec[:3]))
    if n <= 0:
        raise ValueError(f"zero-length vector: {vec!r}")
    return [float(x) / n for x in vec[:3]]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a[:3], b[:3]))


def union_box(boxes: Iterable[Sequence[float]]) -> list[float]:
    """合并若干 [xmin,ymin,zmin,xmax,ymax,zmax]。"""
    bs = [list(map(float, b)) for b in boxes if b and len(b) >= 6]
    if not bs:
        raise ValueError("union_box: 没有可合并的 box")
    return [
        min(b[0] for b in bs), min(b[1] for b in bs), min(b[2] for b in bs),
        max(b[3] for b in bs), max(b[4] for b in bs), max(b[5] for b in bs),
    ]


# ---------------------------------------------------------------- 开口识别

def pipe_openings(edges: Sequence[Mapping[str, Any]]) -> dict[str, dict]:
    """从开放圆边推出入口/出口的射线。

    原理（由 flow_lids_013/open_circular_edges.json 实测得出）：
      入口管道 在外端 x=-0.615 处有【两圈】同心圆边 r=0.105(内孔) / r=0.120(外径)。
      沿轴线(y=0)打射线会穿过内孔打到内部的 04阀轴 / 11蝶板，
      所以横向偏置必须落在【两圈之间】—— 取中值 (0.105+0.120)/2 = 0.1125。
      参考实现在这一点上用的是 0.110，落在同一个带里。

    出口管道对称：x=+1.115，射线朝 -X。
    """
    out: dict[str, dict] = {}
    for logical, comp in PIPE_COMPONENT.items():
        sign = -1.0 if logical == "inlet" else 1.0
        rows = [
            e for e in edges
            if base_component(e.get("component", "")) == comp
            and abs(_dot(_almost_unit(e.get("normal", (0, 0, 0))), (sign, 0, 0))) > 0.99
        ]
        if not rows:
            raise ValueError(f"{logical}: 在组件 {comp!r} 上找不到法向 ≈ {AXIS_NAME[0]} 的圆边")

        # 取最靠外的那一圈（入口取最小 X，出口取最大 X）
        extreme = min(r["center_m"][0] for r in rows) if sign < 0 else max(r["center_m"][0] for r in rows)
        ring = [r for r in rows if abs(float(r["center_m"][0]) - extreme) < 1e-6]
        radii = sorted({_round(r["radius_m"], 9) for r in ring})
        if len(radii) < 2:
            raise ValueError(f"{logical}: 外端只有 {len(radii)} 圈圆边，需要内孔+外径两圈；拿到 {radii}")

        r_in, r_out = radii[0], radii[-1]
        mid = (r_in + r_out) / 2.0
        c = ring[0]["center_m"]
        start_x = extreme + sign * PIPE_RAY_MARGIN_M  # 向外挪出实体
        out[logical] = {
            "logical": logical,
            "ray_point_m": [_round(start_x), _round(c[1] + mid), _round(c[2])],
            "ray_direction": [-sign, 0.0, 0.0],
            "expect_component": comp,
            "evidence": {
                "outer_extreme_m": _round(extreme),
                "inner_radius_m": _round(r_in),
                "outer_radius_m": _round(r_out),
                "transverse_offset_m": _round(mid),
                "ring_count": len(ring),
                "rule": "外端两圈同心圆边的半径中值",
            },
        }
    return out


def body_port_openings(
    edges: Sequence[Mapping[str, Any]],
    *,
    body_component: str = BODY_COMPONENT,
    transverse_offset_m: float | None = None,
) -> dict[str, dict]:
    """从阀体的 ±Z 同轴圆边族推出上/下开口的射线。

    ⚠️ 这一处**尚未完全标定**（plan §七）。已确认的部分：
      阀体在 z=±0.111 有一对同轴圆 r=27 / r=30，参考射线横向偏置 0.029 正好落在
      [0.027, 0.030] 环带内 —— 所以射线能穿过内孔打到那个端面而不撞到阀轴。
      上开口据此可推：中值 (0.027+0.030)/2 = 0.0285。

    未确认的部分：
      下开口。封盖4 的外端面在 z=-0.2475（那里是 r=32.5/37.5 环带），
      但参考射线也用了横向偏置 0.029，与 (0.0325+0.0375)/2=0.035 不符。
      **第一次开 SolidWorks 时必须补一次标定**，在那之前下开口用参考值，
      靠 S3 的「命中组件 == 预期组件」门禁兜底。

    因此本函数返回的每一项都带 ``calibrated`` 标记；调用方必须逐样本验证命中组件。
    """
    # 阀体的组件名是全名（'8“D94R3Y-CL600C-03阀体-1'），不含 '^'，
    # 所以只能按 token 匹配，不能按相等匹配。
    body_names = sorted({str(e.get("component", "")) for e in edges
                         if body_component in str(e.get("component", ""))})
    if len(body_names) != 1:
        raise ValueError(f"{body_component!r} 匹配到 {len(body_names)} 个组件，无法判定：{body_names}")
    body_name = body_names[0]

    rows = [
        e for e in edges
        if e.get("component") == body_name
        and abs(_dot(_almost_unit(e.get("normal", (0, 0, 0))), (0, 0, 1))) > 0.99
        and abs(float(e["center_m"][0])) < 0.2
    ]
    if not rows:
        raise ValueError(f"在 {body_name!r} 上找不到法向 ≈ Z 的圆边")

    # 端口轴线的横向位置：取所有圆边里出现最多的 (x, y) 圆心 —— 端口各族圆边共心，
    # 而螺栓孔是【偏心】的（围绕轴心排一圈），所以众数能干净地把端口族挑出来。
    keyed = [(_round(r["center_m"][0], 5), _round(r["center_m"][1], 5)) for r in rows]
    axis_x, axis_y = max(set(keyed), key=keyed.count)
    on_axis = [r for r in rows
               if abs(r["center_m"][0] - axis_x) < 1e-4 and abs(r["center_m"][1] - axis_y) < 1e-4]

    out: dict[str, dict] = {}
    for logical, sign in (("upper_body", 1.0), ("lower_body", -1.0)):
        side = [r for r in on_axis if float(r["center_m"][2]) * sign > 0]
        if not side:
            raise ValueError(f"{logical}: 阀体在 {sign:+.0f}Z 侧的端口轴线上没有圆边")

        # 内孔环带 = 端口族里最靠轴心的一对同心圆（螺栓孔已由 on_axis 排除）
        innermost = sorted({_round(r["radius_m"], 9) for r in side})[:2]
        if len(innermost) < 2:
            raise ValueError(f"{logical}: 端口轴上只找到 {len(innermost)} 个半径，无法定出内孔环带")
        derived = (innermost[0] + innermost[1]) / 2.0

        if transverse_offset_m is not None:
            offset, calibrated = float(transverse_offset_m), True
        elif logical == "upper_body":
            offset, calibrated = derived, True
        else:
            # 见 docstring：下开口待标定，先用参考值
            offset, calibrated = 0.029, False

        # 起点取该侧最外端再向外一点；方向朝内
        far = max((abs(float(r["center_m"][2])) for r in side), default=0.0)
        z0 = sign * (far + 0.05)

        out[logical] = {
            "logical": logical,
            "ray_point_m": [_round(axis_x + offset), _round(axis_y), _round(z0)],
            "ray_direction": [0.0, 0.0, -sign],
            "expect_component": body_name,
            "calibrated": calibrated,
            "evidence": {
                "axis_x_m": _round(axis_x),
                "axis_y_m": _round(axis_y),
                "innermost_pair_m": [_round(x) for x in innermost],
                "derived_offset_m": _round(derived),
                "used_offset_m": _round(offset),
                "side_extreme_z_m": _round(far * sign),
                "rule": "内孔环带半径中值；下开口待标定",
            },
        }
    return out


def openings_from_edges(
    edges: Sequence[Mapping[str, Any]],
    *,
    ray_start_overrides: Mapping[str, float] | None = None,
    **kw,
) -> dict[str, dict]:
    """四个逻辑开口的完整射线表。

    ``ray_start_overrides`` 允许按逻辑名覆盖【沿射线方向的起点坐标】（入口/出口是 X，
    上下开口是 Z）。用途：上/下开口的横向偏置已经推对了（0.0285 vs 参考 0.029），
    但**沿射线从哪儿起步还没离线证实** —— 参考实现在上开口从阀体内部的 z=0.3 起步，
    推导值给的是外面的 z=0.366。两者原理上都能到达目标面，但只有实测能定。
    第一次标定后把实测值写进 config/flow_physics_reference.json 即可，
    不必改代码。键是逻辑名，值是坐标（米）。
    """
    result = pipe_openings(edges)
    result.update(body_port_openings(edges, **kw))
    for logical, value in (ray_start_overrides or {}).items():
        if logical not in result:
            raise ValueError(f"ray_start_overrides 里有未知的开口 {logical!r}")
        o = result[logical]
        axis = 0 if logical in PIPE_COMPONENT else 2
        before = o["ray_point_m"][axis]
        o["ray_point_m"][axis] = _round(float(value))
        o["start_overridden"] = True
        o.setdefault("evidence", {})["ray_start_override"] = {
            "axis": AXIS_NAME[axis], "derived_m": before, "override_m": _round(float(value)),
        }
    missing = [k for k in LOGICAL_OPENINGS if k not in result]
    if missing:
        raise ValueError(f"开口识别不完整，缺 {missing}")
    return {k: result[k] for k in LOGICAL_OPENINGS}


def ray_select_args(opening: Mapping[str, Any], append: bool) -> list:
    """构造 SelectByRay 的位置参数（顺序与 Create-FlowLids.py 实测一致）。"""
    p = list(opening["ray_point_m"])
    d = list(opening["ray_direction"])
    return p + d + [RAY_RADIUS_M, SELECT_TYPE_FACE, bool(append), 0, 0]


# ---------------------------------------------------------------- 封盖 ↔ 开口

def classify_cap(faces: Sequence[Mapping[str, Any]]) -> dict:
    """判断一个封盖盖住的是哪个开口，并挑出朝向流体内部的那个平面端面。

    faces: 该封盖【全部】面的记录（至少含 box_m / is_plane / plane_params）。

    判开口靠「薄轴 + 符号」：封盖是个薄片，最薄的轴就是开口的轴向；
    入口在 -X、出口在 +X、上开口在 +Z、下开口在 -Z（都由参照文件实测确认）。
    然后再按 INNER_FACE_RULE 在两个平面端面里挑内面。
    """
    if not faces:
        raise ValueError("classify_cap: 没有面记录")
    box = union_box(f.get("box_m") for f in faces)
    extent = [box[3] - box[0], box[4] - box[1], box[5] - box[2]]
    thin_axis = extent.index(min(extent))
    centre = [(box[i] + box[i + 3]) / 2.0 for i in range(3)]

    if thin_axis == 0:
        logical = "inlet" if centre[0] < 0 else "outlet"
    elif thin_axis == 2:
        logical = "upper_body" if centre[2] > 0 else "lower_body"
    else:
        raise ValueError(f"封盖的薄轴是 {AXIS_NAME[thin_axis]}，既不是 X 也不是 Z；box={box}")

    how, axis = INNER_FACE_RULE[logical]
    planes = [f for f in faces if f.get("is_plane") and f.get("plane_params")]
    if len(planes) < 2:
        raise ValueError(f"{logical}: 封盖只有 {len(planes)} 个平面端面，需要两个才谈得上内/外")

    def plane_pos(f):
        return float(f["plane_params"][3 + axis])

    inner = (max if how == "max" else min)(planes, key=plane_pos)
    outer = (min if how == "max" else max)(planes, key=plane_pos)
    r_inner = math.sqrt(float(inner["area_m2"]) / math.pi)

    return {
        "logical": logical,
        "thin_axis": AXIS_NAME[thin_axis],
        "centre_m": [_round(c) for c in centre],
        "extent_m": [_round(e) for e in extent],
        "inner_face": dict(inner),
        "outer_face": dict(outer),
        "inner_plane_value_m": _round(plane_pos(inner)),
        "outer_plane_value_m": _round(plane_pos(outer)),
        "inner_radius_m": _round(r_inner),
        "rule": f"{logical}: {how} {AXIS_NAME[axis]}",
    }


def pair_caps_to_openings(
    caps: Mapping[str, Sequence[Mapping[str, Any]]],
    openings: Mapping[str, Mapping[str, Any]],
    *,
    tol_m: float | Mapping[str, float | None] = 0.001,
) -> dict[str, dict]:
    """把创建出来的封盖按【几何】配到四个逻辑开口上。

    caps: {组件名: [该封盖的面记录, ...]}。组件名**不用来判语义** ——
          实测封盖编号不稳定（一次建出来是 封盖3,2,1,4，另一次是 封盖4,1,2,3）。

    ``tol_m`` 可以是一个数，也可以是**按开口给容差**的字典；某项给 ``None`` 表示
    只记录差值、不作为失败判据。

    ⚠️ 为什么需要按开口给容差：`open_circular_edges.json` 旁边那份
    `flow_lid_reference_faces.json` 记的是**模板的原生封盖**，不是 `TB_CloseHole()`
    的产物。实测（2026-09-21）下开口两者不一致 —— 原生封盖内面在 z=-0.2377，
    而 `TB_CloseHole` 在 z=-0.1017 造盖（几乎是上开口的镜像），且**改射线起点
    从 -0.303 到 -0.4 也改变不了**（射线打到的就是同一个面）。
    所以下开口的参照值不权威，只能记录、不能判否。
    真正的功能判据在 S8：流体域必须密封（`ProblemType == 1`）。
    """
    classified: dict[str, list[dict]] = {k: [] for k in LOGICAL_OPENINGS}
    for comp, faces in caps.items():
        info = classify_cap(faces)
        info["component"] = comp
        classified[info["logical"]].append(info)

    problems = []
    result: dict[str, dict] = {}
    for logical in LOGICAL_OPENINGS:
        got = classified[logical]
        if len(got) != 1:
            problems.append(f"{logical}: 配到 {len(got)} 个封盖（应为 1）—— {[g['component'] for g in got]}")
            continue
        info = got[0]
        ref = openings.get(logical, {}).get("reference_inner_plane")
        if ref is not None:
            delta = abs(info["inner_plane_value_m"] - float(ref))
            info["reference_delta_m"] = _round(delta)
            limit = tol_m.get(logical, 0.001) if isinstance(tol_m, Mapping) else tol_m
            if limit is None:
                info["reference_check"] = "only_recorded"
            elif delta > limit:
                problems.append(
                    f"{logical}: 内面位置 {info['inner_plane_value_m']} 与参照 {ref} 差 {delta:.6f} m，"
                    f"超过 {limit} m")
        result[logical] = info

    if problems:
        raise ValueError("封盖↔开口配对失败：\n  " + "\n  ".join(problems))
    return result


# ---------------------------------------------------------------- 应用层

def check_ray_hits(
    hits: Sequence[Mapping[str, Any]],
    openings: Mapping[str, Mapping[str, Any]],
) -> dict:
    """S3 门禁：四次 SelectByRay 是否都命中了【预期组件】。

    只查组件是不够的 —— 报告里也记下命中的是谁，便于失败时定位。
    """
    seen = {}
    problems = []
    for h in hits:
        logical = h.get("logical") or h.get("name")
        if logical not in LOGICAL_OPENINGS:
            problems.append(f"未知的逻辑开口 {logical!r}")
            continue
        expect = openings[logical]["expect_component"]
        got = base_component(h.get("component") or "")
        seen[logical] = {"expected": expect, "got": got, "selected": bool(h.get("selected"))}
        if not h.get("selected"):
            problems.append(f"{logical}: SelectByRay 没选中任何面")
        elif got != expect:
            problems.append(f"{logical}: 命中 {got!r}，预期 {expect!r}")
    missing = [k for k in LOGICAL_OPENINGS if k not in seen]
    if missing:
        problems.append(f"没有射线的开口：{missing}")
    if problems:
        raise ValueError("开口射线门禁失败：\n  " + "\n  ".join(problems))
    return seen


# ---------------------------------------------------------------- 训练行

TRAINING_COLUMNS = (
    "c_mm", "e_mm", "phi_deg", "alpha_deg", "Dmax_mm", "bm_mm", "ds_mm",
    "SG CV入口静压", "SG CV出口静压", "SG CV入口端面体积流量",
    "SG 蝶板法向压力", "SG 密比压 平均", "SG 密比压 最大", "SG 力矩Z",
    "Cv",
)

CV_Q_FACTOR = 60.0 / 0.003785411784
CV_DP_FACTOR = 6894.757293168


def cv_from_si(q_m3_s: float, delta_p_pa: float, rho_kg_m3: float) -> float:
    """美制液体 Cv。与 valve_mapping/flow.py:cv_from_si 等价，此处为免引 COM 而自带一份。

    ⚠️ rho/1000 是【无量纲相对密度】。直接代 kg/m³ 会偏 sqrt(1000) ≈ 31.6 倍。
    """
    q, dp, rho = float(q_m3_s), float(delta_p_pa), float(rho_kg_m3)
    if not (q > 0):
        raise ValueError(f"体积流量必须为正，得到 {q!r}")
    if not (dp > 0):
        raise ValueError(f"压差必须为正，得到 {dp!r}")
    if not (rho > 0):
        raise ValueError(f"密度必须为正，得到 {rho!r}")
    return (q * CV_Q_FACTOR) * math.sqrt((rho / 1000.0) / (dp / CV_DP_FACTOR))


def cone_face_indices(faces: Sequence[Mapping[str, Any]], *,
                      half_angle_deg: float, tol_deg: float = 0.05,
                      radius_mm: float | None = None, tol_mm: float = 0.5) -> list[int]:
    """找出符合「密封锥面」特征的面的序号。

    判据来自 2026-09-21 的实测导出（`flow_baseline_001/faces_dump.json`）：
    密封锥面族的共同特征是**半锥角 17.75°**（= `alpha_deg` 35.5 的一半），
    而且四个零件上的锥面共享同一条倾斜轴 `[-0.5983, +0.8013, 0]`：

        蝶板   面53  R= 95.6100mm  面积 3976.289mm²
        大垫片 面3   R=124.1103mm  面积  463.210mm²
        密封圈 面3   R=105.2048mm  面积 4648.232mm²   ← 与早先记录的
                                                        R105.2048/ha17.75/area4648.23 逐位吻合
    蝶板那个 R=95.6100 又正好等于 `10压板 D8@草图1 = 95.61`，交叉印证。

    ⚠️ `faces` 必须是**零件局部/世界同序**的同一份清单 —— 返回的序号要拿去
    `sw_api.face_objects(component)` 里取对象，两者顺序必须一致。
    """
    out = []
    for i, f in enumerate(faces):
        if not f.get("is_cone"):
            continue
        cone = f.get("cone_params")
        got_angle, got_radius = _cone_angle_radius(cone)
        if got_angle is None:
            continue
        if abs(got_angle - half_angle_deg) > tol_deg:
            continue
        if radius_mm is not None and abs(got_radius * 1000.0 - radius_mm) > tol_mm:
            continue
        out.append(i)
    return out


def _cone_angle_radius(cone) -> tuple[float | None, float]:
    """`ConeParams` 有两种形态，两种都要吃：

    * `sw_api.face_local_records` 给的是 SolidWorks 的**原始 8 元数组**
      `[顶点(3), 轴向(3), 半径, 半锥角(弧度)]`
    * `enumerate_faces` 导出的 JSON 给的是**转好的字典**
      `{apex_world_m, axis_world, radius_m, half_angle_deg}`

    只认一种，另一条路就会静默返回空表 —— 表现为"密封面一个都没找到"。
    """
    if isinstance(cone, Mapping):
        angle = cone.get("half_angle_deg")
        radius = cone.get("radius_m")
        return (float(angle), float(radius)) if angle is not None and radius is not None else (None, 0.0)
    seq = list(cone or [])
    if len(seq) < 8:
        return None, 0.0
    return math.degrees(float(seq[7])), float(seq[6])


def build_training_row(
    design: Mapping[str, Any],
    goals: Mapping[str, float],
    rho_kg_m3: float,
) -> dict:
    """按 §2 契约构造 15 列训练行。

    缺标签**留空(None)，不得补零**（response_contract.json: missing_label_policy）。
    但只要三个 CV 目标齐备，就一定要算出 Cv —— 缺任何一个就整行不成立。
    """
    row: dict[str, Any] = {}
    for var in TRAINING_COLUMNS[:7]:
        if var not in design:
            raise ValueError(f"设计输入缺 {var}")
        row[var] = float(design[var])

    p_in = goals.get("SG CV入口静压")
    p_out = goals.get("SG CV出口静压")
    q = goals.get("SG CV入口端面体积流量")
    for name, value in (("SG CV入口静压", p_in), ("SG CV出口静压", p_out), ("SG CV入口端面体积流量", q)):
        if value is None or not math.isfinite(float(value)):
            raise ValueError(f"缺 {name}，无法计算 Cv（不得补零，整行不成立）")

    delta_p = float(p_in) - float(p_out)
    cv = cv_from_si(float(q), delta_p, rho_kg_m3)

    for name in TRAINING_COLUMNS[7:-1]:
        value = goals.get(name)
        row[name] = None if value is None else float(value)
    row["Cv"] = cv
    return {col: row.get(col) for col in TRAINING_COLUMNS}


def training_row_matches_contract(row: Mapping[str, Any]) -> list[str]:
    """返回不满足契约的原因列表；空列表表示合格。"""
    problems = []
    if list(row.keys()) != list(TRAINING_COLUMNS):
        problems.append(f"列名或顺序不符：{list(row.keys())}")
    for name, value in row.items():
        if value is None:
            if name == "Cv":
                problems.append("Cv 不得为空")
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            problems.append(f"{name} 不是有限数：{value!r}")
    return problems
