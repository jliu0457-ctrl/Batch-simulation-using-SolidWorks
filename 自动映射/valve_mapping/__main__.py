"""Command line for checked calculations and run preparation; never starts CAD."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .engineering import (
    required_sealing_pressure, sealing_pressure, torque_case, torque_peak,
)
from .flow import cv_from_si, inspect_flow_project, zeta_from_cv, zeta_from_si
from .runs import TASK_ROOT, canonical_hash, create_run, validate_design
from .cad_bridge import import_cad_geometry_run


def _reject_constant(value):
    raise ValueError("nonfinite JSON number is forbidden: " + value)


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON field: " + key)
        out[key] = value
    return out


def _json_input(value):
    stripped = value.lstrip()
    if stripped.startswith(("{", "[")):
        text = value
    else:
        text = Path(value).read_text(encoding="utf-8-sig")
    parsed = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_object)
    if not isinstance(parsed, dict):
        raise ValueError("JSON input must be an object")
    return parsed


def _inside_task(path):
    given = Path(path)
    if not given.is_absolute():
        given = TASK_ROOT / given
    resolved = given.resolve()
    base = TASK_ROOT.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise ValueError("output path must stay strictly inside 自动映射: " + str(base))
    return resolved


def _output_path(value):
    if value is None:
        return None
    path = _inside_task(value)
    if path.suffix.lower() != ".json":
        raise ValueError("--out must name a .json file")
    if path.exists():
        raise FileExistsError("refusing to overwrite existing output: " + str(path))
    return path


def _emit(data, output):
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output is None:
        print(encoded, end="")
        return
    # Re-check after computation; exclusive create refuses races and old results.
    output = _output_path(str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
    print(json.dumps({"output": str(output)}, ensure_ascii=False))


def _engineering(data):
    operation = data.get("operation")
    constants = data.get("constants", "workbook")
    if operation == "required_sealing_pressure":
        if constants not in ("workbook", "mathematical"):
            raise ValueError("constants must be workbook or mathematical")
        params = data.get("params")
        return {
            "q_MF_MPa": required_sealing_pressure(params),
            "material_branch": params["material_branch"],
            "label_kind": "required_sealing_pressure",
            "note": "Required seating pressure, not q_calc or FE contact stress",
        }
    if operation == "sealing_pressure":
        return sealing_pressure(data.get("params"), constants=constants)
    if operation == "torque_case":
        return torque_case(data.get("params"), constants=constants)
    if operation == "torque_peak":
        return torque_peak(
            data.get("cases"), data.get("case_set_id"), constants=constants,
            peak_mode=data.get("peak_mode", "workbook_max"),
            expected_case_ids=data.get("expected_case_ids"),
        )
    raise ValueError(
        "operation must be required_sealing_pressure, sealing_pressure, torque_case, or torque_peak"
    )


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m valve_mapping",
        description="七变量 CAD/仿真映射辅助接口；本工具不启动 CAD 或求解器。",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit-flow", help="只读审查 Flow 工程，不发布训练标签")
    audit.add_argument("project", help="含 .xmlconfig 的项目目录或 .xmlconfig 文件")
    cv = sub.add_parser("cv", help="以明确 SI 输入换算美制液体 Cv")
    cv.add_argument("--q-m3-s", type=float, required=True)
    cv.add_argument("--delta-p-pa", type=float, required=True)
    cv.add_argument("--rho-kg-m3", type=float, required=True)
    cv.add_argument("--rho-ref-kg-m3", type=float, default=1000.0)
    cv.add_argument("--diameter-ref-mm", type=float,
                    help="参考管道/流道直径，不是 Dmax；提供后同时计算 zeta")
    engineering = sub.add_parser("engineering", help="计算书响应；JSON 文件或内联 JSON")
    engineering.add_argument("input_json")
    design = sub.add_parser("validate-design", help="检查七字段和基本取值；不替代几何校验")
    design.add_argument("input_json")
    run = sub.add_parser("create-run", help="创建独立设计记录，不生成几何或运行求解")
    run.add_argument("input_json", help="design_id、design、protocol，及可选 run_id")
    run.add_argument("--base", default="runs", help="自动映射内的记录目录，默认 runs")
    imported = sub.add_parser("import-cad", help="登记已验收的 CAD、参数回读和九文件证据；不运行求解")
    imported.add_argument("report", help="已完成的 mapping_result.json")
    imported.add_argument("--design-id", required=True)
    imported.add_argument("--run-id", required=True)
    imported.add_argument("--protocol", required=True, help="固定几何协议 JSON 文件")
    imported.add_argument("--base", default="runs", help="自动映射内的独立记录目录")
    for command in (audit, cv, engineering, design, run, imported):
        command.add_argument("--out", help="写至自动映射内的新 .json 文件；省略则输出到终端")
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        output = _output_path(args.out)  # reject invalid write targets before work
        if args.command == "audit-flow":
            result = inspect_flow_project(args.project)
        elif args.command == "cv":
            cv_value = cv_from_si(
                args.q_m3_s, args.delta_p_pa, args.rho_kg_m3,
                rho_ref_kg_m3=args.rho_ref_kg_m3,
            )
            result = {
                "Cv": cv_value, "Cv_definition": "US_gpm/sqrt(psi/SG)",
                "inputs": {"Q_m3_s": args.q_m3_s, "delta_p_Pa": args.delta_p_pa,
                           "rho_kg_m3": args.rho_kg_m3,
                           "rho_ref_kg_m3": args.rho_ref_kg_m3},
                "calculation_only": True, "publishable": False,
                "note": "输入换算结果；同次求解、取压、开度和液体适用条件需由采集协议验证。",
            }
            if args.diameter_ref_mm is not None:
                result["zeta"] = zeta_from_cv(
                    cv_value, args.diameter_ref_mm, rho_ref_kg_m3=args.rho_ref_kg_m3,
                )
                diameter = args.diameter_ref_mm / 1000.0
                result["zeta_direct_check"] = zeta_from_si(
                    args.q_m3_s, args.delta_p_pa, args.rho_kg_m3,
                    math.pi * diameter ** 2 / 4.0,
                )
                result["inputs"]["D_ref_mm"] = args.diameter_ref_mm
        elif args.command == "engineering":
            result = _engineering(_json_input(args.input_json))
        elif args.command == "validate-design":
            design = validate_design(_json_input(args.input_json))
            result = {
                "valid_input_schema": True, "design": design,
                "design_hash": canonical_hash(design),
                "geometry_validated": False, "cad_mapping_verified": False,
                "optimization_bounds_checked": False,
                "note": "仅通过七字段、单位及基本数值检查；未证明 CAD 可重建或几何无干涉。",
            }
        elif args.command == "import-cad":
            run = import_cad_geometry_run(
                _inside_task(args.base), args.design_id, args.report,
                _json_input(args.protocol), run_id=args.run_id,
            )
            result = {
                "run_dir": str(run), "manifest": str(run / "manifest.json"),
                "status": "geometry_verified", "cad_executed": False,
                "solver_started": False, "training_labels_ready": False,
            }
        else:
            payload = _json_input(args.input_json)
            base = _inside_task(args.base)
            run = create_run(
                base, payload.get("design_id"), payload.get("design"),
                payload.get("protocol"), run_id=payload.get("run_id"),
            )
            result = {
                "run_dir": str(run), "manifest": str(run / "manifest.json"),
                "status": "created", "geometry_generated": False,
                "solver_started": False, "training_labels_ready": False,
            }
        _emit(result, output)
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "command": args.command},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
