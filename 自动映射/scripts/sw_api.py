#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pywin32 访问原生 SolidWorks 的兼容层。

**为什么需要这一层**：SolidWorks 的 COM 接口在 pywin32 **晚期绑定**下有一堆不一致 ——
有的成员是属性、有的是方法；有的出参必须传 by-ref VARIANT；有的一调就「找不到成员」。
这些坑不集中处理，就会在空间识别这种地方**静默跑偏**，而症状只是"射线打到了别的零件"，
排查成本极高。

下面这些行为都是 2026-09-21 在本机 SolidWorks 2026 SP3.2 (Rev 34.3.2) 上**实测**出来的，
不是照文档抄的：

==============================================  ==========================================
调用                                             实测行为
==============================================  ==========================================
``comp.GetBodies3(0, None)``                     ✗ 类型不匹配
``comp.GetBodies3(0, VARIANT(VT_BYREF|VT_VAR))``  ✓ 返回 tuple，``[0]`` 是 IBody2
``comp.GetModelDoc2()``                          ✗ 找不到成员 —— 用 ``sw.GetOpenDocumentByName``
``comp.Transform2``                              ✓ 属性（16 项列主序）
``comp.GetTotalTransform(True)``                 ✓ 方法
``comp.GetPathName``                             ✓ **属性**（加括号会 'str' object is not callable）
``body.GetFaces()`` / ``body.GetEdges()``        ✓ 方法
``face.GetArea`` / ``face.GetBox``               ✓ **属性**
``face.GetSurface``                              ✓ **属性**
``edge.GetCurve`` / ``GetStartVertex``           ✓ **属性**
``part.SetEntityName(face, name)``               ✓ 方法，且在**零件文档**上，不在面上
``part.GetEntityName(face)``                     ✓ 方法
``sw.OpenDoc6(...)``                             ✓ 返回 **tuple** ``(doc, errors, warnings)``
``gencache.EnsureModule("{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 34, 0)``  ✓ 可用
==============================================  ==========================================
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

#: SolidWorks 类型库 GUID + 版本（实测可用）。当前不需要早期绑定，留作回退。
SW_TYPELIB_GUID = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"
SW_TYPELIB_MAJOR, SW_TYPELIB_MINOR = 0, 34


def safe_get(obj: Any, *names: str, default: Any = None) -> Any:
    """按顺序取**第一个存在的**成员；是 Python 方法就调用，否则原样返回。

    这一个函数消掉了「属性 vs 方法」的歧义 —— 上表里那些 ✓ 属性 / ✓ 方法
    的差别在这一层被抹平，调用方不用关心。

    ⚠️ **不能用 `callable()` 当判据。** pywin32 的 dispatch 对象（比如
    `IComponent2.Transform2` 返回的 `IMathTransform`）**本身看起来就是 callable 的**
    —— 用 `callable()` 判会把属性也调一遍，调失败后当成"取不到"，最后返回 None，
    表现为「坐标变换数据为空」这种跟真实原因毫不相干的症状。
    实测踩过：`Transform2` 因此返回 None，报的是 `NoneType is not iterable`。
    所以这里只对真正的 Python 函数/方法（`FunctionType` / `MethodType` /
    `BuiltinFunctionType`）发起调用。

    ⚠️ **`None` 是合法返回值，不会导致去试下一个名字。**
    `Edge.GetStartVertex` 对整圆就返回 `None` —— 那正是「这条边是闭合圆」的判据，
    当成"取不到"会把所有整圆边误判成被裁剪的边。
    （`flow_transfer.safe_get` 语义不同：它用来在多个访问器里挑一个读得出值的，
    所以会跳过 None。两者不能混用。）
    """
    if obj is None:
        return default
    for name in names:
        try:
            member = getattr(obj, name)
        except Exception:  # noqa: BLE001
            continue
        if _is_python_callable(member):
            try:
                return member()
            except Exception:  # noqa: BLE001
                continue
        return member
    return default


def _is_python_callable(member: Any) -> bool:
    """只认真正的 Python 函数/方法 —— COM dispatch 对象的 callable 是假象。"""
    import types
    return isinstance(member, (types.FunctionType, types.MethodType,
                               types.BuiltinFunctionType, types.MethodWrapperType))


def as_list(value: Any) -> list:
    """COM 数组归一化成 list。单个对象（pywin32 会把只有一个元素的数组塌缩）包成一元表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
        try:
            return list(value)
        except TypeError:
            pass
    return [value]


def out_variant():
    """构造 by-ref VARIANT，用于 `GetBodies3` 这类带 `[out]` 参数的方法。

    **必须传它**：直接传 `None` 会得到「类型不匹配」，而且是在空间识别的深处才炸。
    """
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)


def unwrap_open_doc6(result: Any) -> tuple[Any, int, int]:
    """`sw.OpenDoc6` 在 pywin32 下返回 ``(doc, errors, warnings)``，不是只有 doc。"""
    if isinstance(result, tuple):
        doc = result[0] if len(result) > 0 else None
        errors = int(result[1]) if len(result) > 1 and result[1] is not None else 0
        warnings = int(result[2]) if len(result) > 2 and result[2] is not None else 0
        return doc, errors, warnings
    return result, 0, 0


# ---------------------------------------------------------------- 组件

def component_list(document, *, top_level_only: bool = False) -> list:
    """装配体的组件清单。

    ⚠️ `GetComponents` 是**带参数的方法**（`GetComponents(ToplevelOnly)`），
    `safe_get` 只会无参调用，直接用它只会拿到空表 —— 而且**不报错**，
    表现为"面数 0、边数 0"，看不出是调用方式的问题。
    """
    if document is None:
        return []
    for args in ((top_level_only,), ()):
        try:
            got = document.GetComponents(*args)
        except Exception:  # noqa: BLE001
            continue
        return as_list(got)
    return []


def component_bodies(component) -> list:
    """组件的实体。**不能先取零件文档** —— `GetModelDoc2` 在晚期绑定下不可用。"""
    result = component.GetBodies3(0, out_variant())
    if isinstance(result, tuple) and result:
        return as_list(result[0])
    return as_list(result)


def component_path(component) -> str:
    """组件的零件路径。`GetPathName` 是属性，加括号会报 'str' object is not callable。"""
    return str(safe_get(component, "GetPathName", "PathName", default="") or "")


def component_part_document(sw, component):
    """组件对应的**零件文档**。

    `IComponent2.GetModelDoc2` 在 pywin32 晚期绑定下报「找不到成员」，
    所以绕道 `sw.GetOpenDocumentByName(GetPathName)` —— 组件的零件本来就是已加载的。
    名字相关的操作（`SetEntityName` / `GetEntityName`）在**文档**上，不在面上。
    """
    path = component_path(component)
    if not path:
        return None
    return sw.GetOpenDocumentByName(path)


def transform_array(transform) -> list[float]:
    """`IMathTransform.ArrayData` → 16 项列主序矩阵。

    数据在 **`ArrayData`** 里，不在变换对象本身上 —— 直接 `list(transform)` 会失败。
    """
    if transform is None:
        raise RuntimeError("变换对象为空")
    data = [float(x) for x in list(safe_get(transform, "ArrayData", default=[]))]
    if len(data) < 12:
        raise RuntimeError(f"变换矩阵数据不足 12 项：{len(data)}")
    return data


def component_transform(component) -> list[float]:
    """16 项列主序变换矩阵：[0..2]=X轴 [3..5]=Y轴 [6..8]=Z轴 [9..11]=原点 [12..15]=多余。"""
    return transform_array(safe_get(component, "Transform2"))


def total_transform(component):
    """含装配层级的变换。点用法向/原点区分靠调用方（见 flow_geometry 的变换函数）。"""
    return component.GetTotalTransform(True)


# ---------------------------------------------------------------- 面 / 边

def face_records(component, *, transform: list[float] | None = None) -> Iterator[dict]:
    """一个组件全部面的记录（含局部 box / 面积 / 平面参数），**坐标为零件局部系**。

    调用方负责用 `transform` 转到世界系。
    """
    local = face_local_records(component)
    for rec in local:
        rec["component"] = str(safe_get(component, "Name2", default="") or "")
    return local


def face_local_records(component) -> list[dict]:
    out = []
    for body in component_bodies(component):
        for face in as_list(safe_get(body, "GetFaces")):
            surface = safe_get(face, "GetSurface")
            # ⚠️ 这两个在 pywin32 晚期绑定下是**属性，不是方法** —— 实测
            # `face.GetEdges` 直接就是 tuple、`face.GetLoopCount` 直接就是 int；
            # 加括号调用会抛 `TypeError: 'tuple' object is not callable`，
            # 被 except 吞掉之后**静默记成 0**，签名退化成纯类型、看起来还挺正常。
            # 所以走 `safe_get`（属性访问）才对 —— safe_get 只对真正的 Python
            # 函数/方法发起调用，COM 属性原样返回。别改回加括号的形式。
            edge_count = len(as_list(safe_get(face, "GetEdges")))
            loop_count = int(safe_get(face, "GetLoopCount", default=0) or 0)
            rec: dict[str, Any] = {
                "area_m2": abs(float(safe_get(face, "GetArea", default=0.0) or 0.0)),
                "box_m": [float(v) for v in as_list(safe_get(face, "GetBox"))][:6],
                "edge_count": edge_count,
                "loop_count": loop_count,
                "is_plane": bool(safe_get(surface, "IsPlane", default=False)),
                "is_cone": bool(safe_get(surface, "IsCone", default=False)),
                "is_cylinder": bool(safe_get(surface, "IsCylinder", default=False)),
            }
            # 三个已知类型都不成立时，才去问其余曲面类型。这是 `?` 面的唯一线索：
            # 圆角是 IsTorus、重建退化被替换出来的近似面是 IsBSurface —— 两者结论相反。
            # 短路是为了不给每个面都加 7 次 COM 调用（整台装配体六七百个面）。
            if not (rec["is_plane"] or rec["is_cone"] or rec["is_cylinder"]):
                rec["other_surface"] = [name for name in (
                    "IsSphere", "IsTorus", "IsBSurface", "IsSwept",
                    "IsExtruded", "IsRevolution", "IsOffset",
                ) if bool(safe_get(surface, name, default=False))]
            if rec["is_plane"]:
                rec["plane_params"] = [float(v) for v in as_list(safe_get(surface, "PlaneParams"))][:6]
            if rec["is_cone"]:
                rec["cone_params"] = [float(v) for v in as_list(safe_get(surface, "ConeParams"))][:8]
            if rec["is_cylinder"]:
                rec["cylinder_params"] = [
                    float(v) for v in as_list(safe_get(surface, "CylinderParams"))][:7]
            out.append(rec)
    return out


def face_objects(component) -> list:
    """面的 COM 对象本身（命名/选中要用），与 `face_local_records` 同序。"""
    out = []
    for body in component_bodies(component):
        out.extend(as_list(safe_get(body, "GetFaces")))
    return out


def circular_edges(component) -> list[dict]:
    """组件的圆边：零件局部系的圆心/法向/半径 + 是否整圆。"""
    out = []
    for body in component_bodies(component):
        for edge in as_list(safe_get(body, "GetEdges")):
            curve = safe_get(edge, "GetCurve")
            if curve is None:
                continue
            if not bool(safe_get(curve, "IsCircle", default=False)):
                continue
            params = [float(v) for v in as_list(safe_get(curve, "CircleParams"))]
            if len(params) < 7:
                continue
            out.append({
                "center_m": params[0:3],
                "normal": params[3:6],
                "radius_m": params[6],
                "closed": (safe_get(edge, "GetStartVertex") is None
                           and safe_get(edge, "GetEndVertex") is None),
            })
    return out


# ---------------------------------------------------------------- 实体命名

def callout_none():
    """`Select4` 第二个参数（Callout）的「空」必须写成 **VT_DISPATCH 的空 VARIANT**。

    实测（2026-09-21）：
        ``Select4(True, None)``                  ✗ 类型不匹配
        ``Select4(True, pythoncom.Empty)``       ✗ 类型不匹配
        ``Select4(True, VARIANT(VT_EMPTY,None))``✗ 类型不匹配
        ``Select4(True, VARIANT(VT_DISPATCH,None))`` ✓ 选中 1 个
    C# 那边传 `null` 就行，所以这是 **pywin32 特有的编组差异**，查文档查不出来。
    """
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_DISPATCH, None)


def select_face(face, append: bool = False) -> bool:
    """选中一个面。返回是否成功。"""
    return bool(face.Select4(bool(append), callout_none()))


def set_entity_name(sw, component, face_index: int, name: str) -> dict:
    """给组件上第 `face_index` 个面打稳定名字。返回回读结果。

    ⚠️ `SetEntityName` 在**零件文档**上（`part.SetEntityName(face, name)`），不在面上；
    面上的 `face.SetEntityName` 在晚期绑定下报「找不到成员」。
    """
    faces = face_objects(component)
    if not 0 <= face_index < len(faces):
        raise IndexError(f"面序号 {face_index} 越界（该组件 {len(faces)} 个面）")
    part = component_part_document(sw, component)
    if part is None:
        raise RuntimeError(f"拿不到 {component_path(component)!r} 的零件文档")
    face = faces[face_index]
    before = str(part.GetEntityName(face) or "")
    ok = bool(part.SetEntityName(face, name))
    after = str(part.GetEntityName(face) or "")

    # ⚠️ **必须立刻保存零件。** 实体名字存在【零件文档】里，不保存就随文档关闭而消失 ——
    # 实测踩过：命名读回成功，但下一次运行时名字没了，选择回调选中 0 个面，
    # 报出来的却是"绑定失败"，完全看不出是名字丢了。
    #
    # ⚠️ `IModelDoc2.Save()` 在 pywin32 下**返回 None**（void 方法），不能拿返回值判成败。
    # 也不能用**文件时间戳**判 —— Windows（NTFS）会延迟刷新时间元数据，
    # 刚写完 stat 到的可能还是旧值，会误判成"没落盘"（实测踩过）。
    # 用 SolidWorks 自己的信号：`GetSaveFlag()` 表示"有未保存改动"，
    # 保存后应当变成 False。
    saved, detail = None, None
    path = component_path(component)
    flag_before = safe_get(part, "GetSaveFlag", default=None)
    try:
        part.Save()
    except Exception as exc:  # noqa: BLE001
        detail = f"Save() 抛异常：{type(exc).__name__}: {exc}"
    else:
        flag_after = safe_get(part, "GetSaveFlag", default=None)
        if flag_after is None:
            detail = ("读不到 GetSaveFlag，无法核实是否落盘 —— "
                      "名字有可能随文档关闭而丢失")
        else:
            saved = not bool(flag_after)
            if not saved:
                detail = "Save() 之后 GetSaveFlag 仍为真 —— 没落盘"

    return {"component": str(safe_get(component, "Name2", default="") or ""),
            "index": face_index, "set": ok, "previous": before,
            "readback": after, "part_path": path,
            "save_flag_before": flag_before, "part_saved": saved, "save_detail": detail,
            "ok": ok and after == name and saved is not False}
