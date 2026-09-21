#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：探 `FDAProject.GlobalMeshSettings` 里到底能读/写哪些参数。

**为什么值得探。** 有一批设置既不在 `CalculationControlOptions`（那 14 个属性里没有），
也拿不到 —— 但可能有另一条路：官方帮助里写了

    Set PS = FDA.GlobalMeshSettings.GetInterface("IParametrizedFeature")

也就是**全局网格设置本身就是一个 ParametrizedFeature**，和局部网格同一套机制，
可以 `EnumParameters()` 逐个读、`SetLongValue/SetValue/...` 逐个写。

想找的目标（这些在 `1.xmlconfig` 的 `<ConvergenceOptions>` 块里，公开 API 的
`CalculationControlOptions` **没有**对应属性）：

    RefLevel       —— 自适应网格细化级别（人工 2 / 我们 0）
    RefMaxCells    —— 自适应细化的单元上限（人工 1 / 我们 27800000）
    AutorefStart / AutorefPeriod / RefStrategy / RefRelax

**顺带也读全局的** `CellPerGap` / `ResultResolution` / 各级细化 —— 万一能写，
`CellPerGap` 的窘境就解了。

用法：
    python scripts/Probe-GlobalMeshSettings.py [输出.json]
    python scripts/Probe-GlobalMeshSettings.py --use-open-doc [输出.json]

只读：**不写任何参数**（只调 getter）。`--use-open-doc` 读用户已经打开的那个文档，
不开也不关任何东西。
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

import flow_project as fpj   # noqa: E402
import flow_session as fses  # noqa: E402
import sw_api as swa         # noqa: E402

HUMAN_ASSEMBLY = REPO_ROOT / '8“D94R3Y-CL600C-00组装图.SLDASM'
CONFIG = "开度45°"


def dump_parameters(feature) -> list[dict]:
    """和 `Probe-FlowLocalMesh` 同一套：四个访问器各自的原始返回（含错误码）。"""
    out = []
    for p in fpj._enum_parameters(feature):
        entry: dict = {"type": int(p.Type)}
        raws: dict = {}
        for attr, key in (("GetValue", "value"), ("GetLongValue", "long"),
                          ("GetBoolValue", "bool"), ("GetStringValue", "string")):
            try:
                got = getattr(p, attr)()
            except Exception as exc:  # noqa: BLE001
                raws[attr] = f"EXC {type(exc).__name__}"
                continue
            raws[attr] = repr(got)
            u = fpj.unwrap_getter(got)
            if u is None:
                continue
            if isinstance(u, bool):
                entry[key] = bool(u)
            elif isinstance(u, str):
                if u:
                    entry[key] = u
            else:
                f = float(u)
                entry[key] = int(f) if f == int(f) and abs(f) < 2 ** 31 else f
        entry["_raw"] = raws
        out.append(entry)
    return out


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    use_open = "--use-open-doc" in argv
    if use_open:
        argv.remove("--use-open-doc")
    out_path = Path(argv[0]) if argv else (MAPPING_ROOT / "_analysis" / "global_mesh_probe.json")

    session = fses.SwSession.attach()
    report: dict = {"mode": "use_open_doc" if use_open else "attach"}
    opened_by_us = False
    try:
        if use_open:
            document = session.interactive.ActiveDocument
            if document is None:
                raise RuntimeError("没有打开任何文档")
            report["active_doc"] = str(swa.safe_get(document, "Name", default="") or "")
        else:
            report["gate"] = session.gate_or_raise()
            document = fpj.open_assembly(session, HUMAN_ASSEMBLY, CONFIG)
            opened_by_us = True
        configuration = fpj.active_configuration(document, CONFIG)
        names = [str(n) for n in (configuration.GetProjectNames() or [])]
        report["projects"] = names
        if not names:
            raise RuntimeError("这个装配体里没有 Flow 工程")
        project = configuration.ActivateProject(names[0], False)

        gms = project.GlobalMeshSettings
        report["GlobalMeshSettings_type"] = type(gms).__name__
        # 它自己有没有 EnumParameters（有些对象直接就是 ParametrizedFeature）
        direct = dump_parameters(gms) if hasattr(gms, "EnumParameters") else []
        report["GlobalMeshSettings_direct"] = {"has_EnumParameters": bool(direct), "parameters": direct}
        # 官方路子：GetInterface("IParametrizedFeature")
        try:
            ps = gms.GetInterface("IParametrizedFeature")
            report["GetInterface_IParametrizedFeature"] = (
                {"is_none": ps is None, "parameters": dump_parameters(ps) if ps is not None else []})
        except Exception as exc:  # noqa: BLE001
            report["GetInterface_error"] = f"{type(exc).__name__}: {exc}"
        # 直接列一下它有哪些成员名，便于找别的入口
        try:
            report["members"] = [m for m in dir(gms) if not m.startswith("_")][:60]
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if opened_by_us:
                session.close_all_documents()
            else:
                report["close_skipped"] = "本模式不开也不关任何文档"
        finally:
            session.unload()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "error": report.get("error")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
