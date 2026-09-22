#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 V6 母版的哈希一致副本导出装配体组件面序列；只读，不运行 Flow。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUNS = ROOT / "working" / "seven_variable_trials"
MANIFEST = ROOT / "config" / "cad_template_manifest_v6.json"
for item in (ROOT.parent, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import flow_project as fpj  # noqa: E402
import flow_session as fses  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_face_exporter():
    path = SCRIPTS / "Run-FlowSample.py"
    spec = importlib.util.spec_from_file_location("run_flow_sample_faces", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.enumerate_faces


def kind(record: dict) -> str:
    if record.get("is_plane"):
        return "P"
    if record.get("is_cone"):
        return "C"
    if record.get("is_cylinder"):
        return "Y"
    return "O"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: Export-TemplateTopology.py <probe-run-name>")
    run = (RUNS / argv[1]).resolve()
    if run.parent != RUNS.resolve() or not run.is_dir():
        raise RuntimeError(f"探测副本必须是 {RUNS} 的直接子目录：{run}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["files"]
    actual_names = sorted(path.name for path in run.glob("*.SLD*"))
    if actual_names != sorted(expected):
        raise RuntimeError(f"探测副本文件集合与母版清单不同：{actual_names}")
    mismatches = [name for name, wanted in expected.items()
                  if sha256(run / name).lower() != str(wanted).lower()]
    if mismatches:
        raise RuntimeError(f"探测副本不是当前母版的哈希一致复制：{mismatches}")

    enumerate_faces = load_face_exporter()
    session = fses.SwSession.attach()
    output = run / "template_topology.json"
    try:
        gate = session.gate_or_raise(run=run)
        assembly = next(run.glob("*.SLDASM"))
        fpj.open_assembly(session, assembly, "开度45°")
        document = session.sw.ActiveDoc
        if document is None:
            raise RuntimeError("打开V6母版副本后没有活动装配体")
        # 参数化链路在拓扑门禁前必定 ForceRebuild3。母版参考也必须处在同一状态；
        # 否则拿磁盘里尚未重建的缓存面序列去比较重建后的副本，会在基准点上误报。
        if not bool(document.ForceRebuild3(False)):
            raise RuntimeError("V6 母版副本强制重建失败，不能冻结拓扑参考")
        faces = enumerate_faces(document)
        parts = {}
        for token in ("03阀体", "04阀轴", "08大垫片", "09密封圈", "10压板", "11蝶板"):
            matches = [(name, rows) for name, rows in faces.items() if token in name]
            if len(matches) != 1:
                raise RuntimeError(f"{token} 组件数不是1：{[name for name, _ in matches]}")
            name, rows = matches[0]
            filename = next(key for key in expected if token in key)
            signatures = sorted(
                f"{kind(row)}|E{int(row.get('edge_count', 0))}|L{int(row.get('loop_count', 0))}"
                for row in rows
            )
            parts[filename] = {
                "component": name,
                "face_count": len(rows),
                "kind_counts": {one: sum(kind(row) == one for row in rows) for one in "PCYO"},
                "topology_signatures": signatures,
            }
        output.write_text(json.dumps({
            "source": str(run),
            "assembly": str(assembly),
            "precondition": "opened in 开度45° and ForceRebuild3(False) completed",
            "extraction_path": "IComponent2.GetBodies3 -> IBody2.GetFaces",
            "gate": gate,
            "parts": parts,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output)
        return 0
    finally:
        session.close_all_documents()
        session.unload()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
