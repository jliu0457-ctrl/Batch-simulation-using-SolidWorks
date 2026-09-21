#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sw_api 的离线测试。

这一层是为了抹平 pywin32 晚期绑定的不一致：有的成员是属性、有的是方法、有的出参必须
传 by-ref VARIANT。**这些差异猜错的后果是空间识别静默跑偏** —— 症状只是"射线打到了
别的零件"，排查成本极高。所以这里用假对象把两种形态都覆盖到。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MAPPING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAPPING / "scripts"))

import sw_api as swa  # noqa: E402


# ---------------------------------------------------------------- safe_get

class PropsOnly:
    """成员是属性（实测：Face.GetArea / GetBox / GetSurface、Edge.GetCurve、
    Comp.GetPathName、Comp.Transform2 都是这一类）。"""
    GetArea = 0.0123
    GetBox = (-1.0, -2.0, -3.0, 1.0, 2.0, 3.0)


class MethodsOnly:
    """成员是方法（实测：Body.GetFaces / GetEdges、Comp.GetTotalTransform）。"""

    def GetFaces(self):
        return ["f1", "f2"]

    def GetStartVertex(self):
        return None


def test_safe_get_handles_property_style():
    assert swa.safe_get(PropsOnly(), "GetArea") == 0.0123
    assert list(swa.safe_get(PropsOnly(), "GetBox")) == [-1.0, -2.0, -3.0, 1.0, 2.0, 3.0]


def test_safe_get_handles_method_style():
    assert swa.safe_get(MethodsOnly(), "GetFaces") == ["f1", "f2"]
    assert swa.safe_get(MethodsOnly(), "GetStartVertex") is None


def test_safe_get_tries_names_in_order():
    obj = PropsOnly()
    assert swa.safe_get(obj, "Nope", "GetArea") == 0.0123


def test_safe_get_returns_default_when_absent():
    assert swa.safe_get(PropsOnly(), "DoesNotExist", default="d") == "d"
    assert swa.safe_get(None, "Anything", default=None) is None


def test_safe_get_keeps_going_when_a_method_raises():
    """晚期绑定下「找不到成员」随时会抛 —— 不能因此认定后面也没戏。"""

    class Flaky:
        def Bad(self):
            raise RuntimeError("找不到成员")

        GetArea = 7.5

    assert swa.safe_get(Flaky(), "Bad", "GetArea") == 7.5


def test_safe_get_does_not_return_none_from_a_method_that_legitimately_returns_none():
    """`GetStartVertex` 对整圆返回 None —— 那是有效值，不能因此去试下一个名字。"""
    calls = []

    class E:
        def GetStartVertex(self):
            calls.append(1)
            return None

        GetStartVertex2 = "should not be reached"

    assert swa.safe_get(E(), "GetStartVertex", "GetStartVertex2") is None
    assert calls == [1]


# ---------------------------------------------------------------- as_list

def test_as_list_normalizes_com():
    assert swa.as_list(None) == []
    assert swa.as_list([1, 2]) == [1, 2]
    assert swa.as_list((1, 2)) == [1, 2]
    assert list(swa.as_list(range(3))) == [0, 1, 2]


def test_as_list_wraps_a_single_object():
    """pywin32 会把只有一个元素的 COM 数组塌缩成裸对象 —— 实测 GetBodies3 就是这样。"""

    class Single:
        pass

    obj = Single()
    assert swa.as_list(obj) == [obj]


def test_as_list_does_not_iterate_a_string():
    assert swa.as_list("abc") == ["abc"]


# ---------------------------------------------------------------- OpenDoc6

def test_unwrap_open_doc6_handles_the_tuple():
    """pywin32 下 `sw.OpenDoc6` 返回 `(doc, errors, warnings)`，不是只有 doc。"""
    doc, err, warn = swa.unwrap_open_doc6(("DOC", 0, 128))
    assert doc == "DOC" and err == 0 and warn == 128


def test_unwrap_open_doc6_handles_a_bare_document():
    doc, err, warn = swa.unwrap_open_doc6("DOC")
    assert doc == "DOC" and err == 0 and warn == 0


def test_unwrap_open_doc6_handles_failure():
    doc, err, warn = swa.unwrap_open_doc6((None, 65536, 0))
    assert doc is None and err == 65536


# ---------------------------------------------------------------- 组件

class FakeComponent:
    def __init__(self, name, path, bodies):
        self.Name2 = name
        self.GetPathName = path
        self._bodies = bodies
        self.calls = []

    def GetBodies3(self, state, out):
        self.calls.append((state, out))
        return (self._bodies,)


class FakeBody:
    def __init__(self, faces=(), edges=()):
        self._faces, self._edges = list(faces), list(edges)

    def GetFaces(self):
        return self._faces

    def GetEdges(self):
        return self._edges


class FakeFace:
    def __init__(self, area, box, plane=None, cone=None, cyl=None):
        self.GetArea = area
        self.GetBox = box
        self._surface = FakeSurface(plane, cone, cyl)

    @property
    def GetSurface(self):          # 实测是属性
        return self._surface


class FakeSurface:
    def __init__(self, plane, cone, cyl):
        self._plane, self._cone, self._cyl = plane, cone, cyl

    def IsPlane(self):
        return self._plane is not None

    def IsCone(self):
        return self._cone is not None

    def IsCylinder(self):
        return self._cyl is not None

    @property
    def PlaneParams(self):
        return self._plane or []

    @property
    def ConeParams(self):
        return self._cone or []

    @property
    def CylinderParams(self):
        return self._cyl or []


def test_component_bodies_passes_a_by_ref_variant():
    """直接传 None 会得到「类型不匹配」—— 必须传 by-ref VARIANT。"""
    comp = FakeComponent("c", "p", [FakeBody()])
    bodies = swa.component_bodies(comp)
    assert len(bodies) == 1
    state, out = comp.calls[0]
    assert state == 0
    assert out is not None, "出参不能是 None"


def test_component_bodies_handles_the_single_body_collapse():
    """实测：组件只有一个实体时，GetBodies3 返回的 [0] 是裸 IBody2，不是数组。"""
    single = FakeBody()
    comp = FakeComponent("c", "p", single)
    assert swa.component_bodies(comp) == [single]


def test_component_path_reads_the_property_without_parens():
    """`GetPathName` 是属性；加括号会 'str' object is not callable。"""
    comp = FakeComponent("c", r"C:\x\y.SLDPRT", [])
    assert swa.component_path(comp) == r"C:\x\y.SLDPRT"


def test_face_local_records_classifies_surfaces():
    faces = [
        FakeFace(0.0366, (-1, -1, -1, 1, 1, 1), plane=[0, 0, 1, 0, 0, 0.1]),
        FakeFace(0.0046, (-1, -1, -1, 1, 1, 1), cone=[0, 0, 0, 0, 0, 1, 0.105, 0.31]),
        FakeFace(0.001, (-1, -1, -1, 1, 1, 1), cyl=[0, 0, 0, 0, 0, 1, 0.05]),
    ]
    comp = FakeComponent("c", "p", [FakeBody(faces=faces)])
    records = swa.face_local_records(comp)
    assert [r["is_plane"] for r in records] == [True, False, False]
    assert [r["is_cone"] for r in records] == [False, True, False]
    assert [r["is_cylinder"] for r in records] == [False, False, True]
    assert records[0]["plane_params"] == [0, 0, 1, 0, 0, 0.1]
    assert records[1]["cone_params"][6] == pytest.approx(0.105)
    # 面对象与记录同序 —— 命名/选中靠这个对应关系
    assert len(swa.face_objects(comp)) == len(records) == 3


def test_circular_edges_reads_params_and_closedness():
    class FakeCurve:
        def __init__(self, circle):
            self._c = circle

        def IsCircle(self):
            return self._c is not None

        @property
        def CircleParams(self):
            return self._c or []

    class FakeEdge:
        def __init__(self, curve, closed):
            self._curve = curve
            self._closed = closed

        @property
        def GetCurve(self):
            return self._curve

        @property
        def GetStartVertex(self):
            return None if self._closed else object()

        @property
        def GetEndVertex(self):
            return None if self._closed else object()

    edges = [FakeEdge(FakeCurve([0.1, 0.2, 0.3, 0, 0, 1, 0.105]), True),
             FakeEdge(FakeCurve(None), False),
             FakeEdge(FakeCurve([0, 0, 0, 1, 0, 0, 0.12]), False)]
    comp = FakeComponent("c", "p", [FakeBody(edges=edges)])

    got = swa.circular_edges(comp)
    assert len(got) == 2, "非圆边要被剔除"
    assert got[0]["center_m"] == [0.1, 0.2, 0.3]
    assert got[0]["radius_m"] == pytest.approx(0.105)
    assert got[0]["closed"] is True
    assert got[1]["closed"] is False


# ---------------------------------------------------------------- 命名

class FakePart:
    def __init__(self):
        self.names = {}
        self.saves = 0

    def GetEntityName(self, face):
        return self.names.get(id(face), "")

    def SetEntityName(self, face, name):
        self.names[id(face)] = name
        return True

    def __init_saveflag__(self):
        if not hasattr(self, "_dirty"):
            self._dirty = True

    def GetSaveFlag(self):
        """有未保存改动时返回 True —— 用它核实保存是否真的发生，**不用文件时间戳**：
        Windows（NTFS）会延迟刷新时间元数据，刚写完 stat 到的可能还是旧值。"""
        return getattr(self, "_dirty", True)

    def Save(self):
        self.saves += 1
        self._dirty = False
        return None      # 实测：pywin32 下 IModelDoc2.Save() 返回 None


class FakeSw:
    def __init__(self, part):
        self._part = part
        self.lookups = []

    def GetOpenDocumentByName(self, path):
        self.lookups.append(path)
        return self._part


def test_set_entity_name_goes_through_the_part_document(tmp_path):
    """`SetEntityName` 在**零件文档**上（`part.SetEntityName(face, name)`），不在面上 ——
    面上的 `face.SetEntityName` 在晚期绑定下报「找不到成员」。

    零件路径必须指向**真实存在**的文件：成败是拿文件时间戳核实的，
    因为 pywin32 下 `Save()` 返回 None、拿不到返回值。
    """
    part_file = tmp_path / "封盖3.SLDPRT"
    part_file.write_bytes(b"dummy")
    faces = [FakeFace(1.0, (0,) * 6, plane=[0, 0, 1, 0, 0, 0]),
             FakeFace(2.0, (0,) * 6, plane=[0, 0, 1, 0, 0, 1])]
    comp = FakeComponent("封盖3-1", str(part_file), [FakeBody(faces=faces)])
    part = FakePart()
    part.touch = part_file
    sw = FakeSw(part)

    result = swa.set_entity_name(sw, comp, 1, "FLOW_UPPER_INNER")
    assert result["ok"] is True
    assert result["set"] is True
    assert result["readback"] == "FLOW_UPPER_INNER"
    assert result["previous"] == ""
    assert result["component"] == "封盖3-1"
    assert sw.lookups == [str(part_file)], "必须按组件路径取零件文档"
    assert result["part_saved"] is True, "名字存在零件文档里，必须落盘"


def test_set_entity_name_fails_when_the_document_stays_dirty(tmp_path):
    """保存没真正发生 —— 名字随文档关闭就消失，下一次运行找不到面。
    不能因为 `Save()` 没抛异常就当成成功。"""
    part_file = tmp_path / "p.SLDPRT"
    part_file.write_bytes(b"dummy")
    comp = FakeComponent("c", str(part_file),
                         [FakeBody(faces=[FakeFace(1.0, (0,) * 6, plane=[0, 0, 1, 0, 0, 0])])])

    class NoOpSave(FakePart):
        def Save(self):
            return None          # 不抛异常，但也不清 save flag

    result = swa.set_entity_name(FakeSw(NoOpSave()), comp, 0, "X")
    assert result["ok"] is False
    assert result["part_saved"] is False
    assert "GetSaveFlag 仍为真" in (result["save_detail"] or "")


def test_set_entity_name_flags_an_unverifiable_save(tmp_path):
    """读不到 `GetSaveFlag` 时不能假装成功，也不能假装失败 —— 必须留下"未核实"。"""
    part_file = tmp_path / "p.SLDPRT"
    part_file.write_bytes(b"dummy")
    comp = FakeComponent("c", str(part_file),
                         [FakeBody(faces=[FakeFace(1.0, (0,) * 6, plane=[0, 0, 1, 0, 0, 0])])])

    class NoFlag(FakePart):
        GetSaveFlag = None          # 这个版本没有这个成员 —— safe_get 会当成取不到

        def Save(self):
            return None

    result = swa.set_entity_name(FakeSw(NoFlag()), comp, 0, "X")
    assert result["part_saved"] is None
    assert "无法核实" in (result["save_detail"] or "")


def test_set_entity_name_reports_a_failed_write():
    faces = [FakeFace(1.0, (0,) * 6, plane=[0, 0, 1, 0, 0, 0])]
    comp = FakeComponent("c", "p", [FakeBody(faces=faces)])

    class RefusingPart(FakePart):
        def SetEntityName(self, face, name):
            return False

    result = swa.set_entity_name(FakeSw(RefusingPart()), comp, 0, "X")
    assert result["ok"] is False and result["set"] is False


def test_set_entity_name_rejects_a_bad_index():
    comp = FakeComponent("c", "p", [FakeBody(faces=[FakeFace(1.0, (0,) * 6)])])
    with pytest.raises(IndexError, match="越界"):
        swa.set_entity_name(FakeSw(FakePart()), comp, 5, "X")


# ---------------------------------------------------------------- 模块卫生

def test_sw_api_imports_without_com():
    source = (MAPPING / "scripts" / "sw_api.py").read_text(encoding="utf-8")
    header = source.split("def safe_get", 1)[0]
    assert "import win32com" not in header
    assert "import pythoncom" not in header


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

def test_safe_get_does_not_call_a_com_object_that_looks_callable():
    """pywin32 的 dispatch 对象看着是 callable 的 —— 不能因此去调用它。

    实测踩过：`IComponent2.Transform2` 被当成方法调用、失败、返回 None，
    最后报的是 `NoneType is not iterable`，跟真实原因毫无关系。
    """

    class FakeDispatch:
        """有 __call__，但不是 Python 函数/方法 —— 模拟 COM dispatch。"""

        def __init__(self, payload):
            self._payload = payload
            self.called = 0

        def __call__(self):
            self.called += 1
            raise RuntimeError("COM 对象不该被调用")

        def __iter__(self):
            return iter(self._payload)

    payload = FakeDispatch([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])

    class Comp:
        Transform2 = payload

    got = swa.safe_get(Comp(), "Transform2")
    assert list(got) == [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    assert payload.called == 0, "属性不能被当方法调用"


def test_component_transform_reads_the_array_data():
    class T:
        ArrayData = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.1, 0.2, 0.3, 1, 0, 0, 0]

    class Comp:
        Transform2 = T()

    assert swa.component_transform(Comp()) == [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.1, 0.2, 0.3, 1, 0, 0, 0]


def test_component_list_calls_getcomponents_with_an_argument():
    """`GetComponents` 带参数；无参调用只会拿到空表，而且**不报错** ——
    表现为"面数 0、边数 0"，看不出是调用方式的问题。"""

    class Doc:
        def __init__(self):
            self.args = []

        def GetComponents(self, top_only):
            self.args.append(top_only)
            return ["c1", "c2"]

    doc = Doc()
    assert swa.component_list(doc) == ["c1", "c2"]
    assert doc.args == [False]


def test_component_list_handles_none_document():
    assert swa.component_list(None) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
