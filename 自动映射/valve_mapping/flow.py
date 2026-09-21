"""Read-only Flow Simulation goal audit; no solver calls or training-label publication.

Surface_Goal type IDs are interpreted only for observed types 0/16/23.
Legacy files do not prove a completed, geometry-matched simulation run.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

US_GALLON_M3 = 0.003785411784
PSI_PA = 6894.757293168
WATER_REFERENCE_KG_M3 = 1000.0
CV_FACTOR_SI = 60.0 / US_GALLON_M3 * math.sqrt(PSI_PA / WATER_REFERENCE_KG_M3)
_TYPES = {
    0: ("fluid_static_pressure", "Pa", "pressure and stress"),
    16: ("volume_flow", "m^3/s", "volumetric flow"),
    23: ("fluid_moment_z", "N*m", "momentum"),
}
_LABELS = {"T_peak_Nm": None, "sigma_n_MPa": None, "q_calc_MPa": None, "Cv": None}

def _number(value, name, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not bool")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return value

def cv_from_si(q_m3_s, delta_p_pa, rho_kg_m3, *, rho_ref_kg_m3=1000.0):
    """Standard US liquid Cv. Flow is a positive magnitude; dp and density > 0.

    Requires compatible pressure taps, nonchoked noncavitating incompressible
    liquid conditions. Caller must validate those physical conditions.
    """
    q = _number(q_m3_s, "q_m3_s", positive=True)
    dp = _number(delta_p_pa, "delta_p_pa", positive=True)
    rho = _number(rho_kg_m3, "rho_kg_m3", positive=True)
    ref = _number(rho_ref_kg_m3, "rho_ref_kg_m3", positive=True)
    return q * 60.0 / US_GALLON_M3 * math.sqrt((rho / ref) / (dp / PSI_PA))

def zeta_from_si(q_m3_s, delta_p_pa, rho_kg_m3, area_ref_m2):
    q = _number(q_m3_s, "q_m3_s", positive=True)
    dp = _number(delta_p_pa, "delta_p_pa", positive=True)
    rho = _number(rho_kg_m3, "rho_kg_m3", positive=True)
    area = _number(area_ref_m2, "area_ref_m2", positive=True)
    return 2 * dp * area**2 / (rho * q**2)

def zeta_from_cv(cv, diameter_ref_mm, *, rho_ref_kg_m3=1000.0):
    """Use reference PIPE/FLOW diameter, not butterfly-disc Dmax."""
    cv = _number(cv, "cv", positive=True)
    diameter = _number(diameter_ref_mm, "diameter_ref_mm", positive=True) / 1000.0
    ref = _number(rho_ref_kg_m3, "rho_ref_kg_m3", positive=True)
    area = math.pi * diameter**2 / 4
    factor = 2 * (60 / US_GALLON_M3)**2 * PSI_PA / ref
    return factor * area**2 / cv**2

def _read(path):
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError(f"Cannot decode {path}")

def _val(node, tag, default=""):
    child = node.find(tag)
    return child.get("value", default) if child is not None else default

def _blocks(text, tag):
    # Only extract explicitly bounded leaf blocks; never rewrite malformed XML.
    for raw in re.findall(r"<" + tag + r"\b[^>]*>.*?</" + tag + r">", text, re.S):
        yield ET.fromstring(raw)

def _hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def _record(path, root):
    stat = path.stat()
    return {"path": path.relative_to(root).as_posix(), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256": _hash(path)}

def _dat(path):
    rows = list(csv.DictReader(io.StringIO(_read(path)), delimiter="\t"))
    if not rows:
        raise ValueError("empty goal history")
    last = {}
    for key, value in rows[-1].items():
        if key and value:
            last[key] = _number(value, key)
    return {"rows": len(rows), "last_row": last,
            "value_mean_difference": last.get("Value", 0) - last.get("AvValue", 0)}

def _difference(a, b):
    if a is None or b is None:
        return None
    return float(a) - float(b)

def inspect_flow_project(path):
    """Audit a project directory, or its .xmlconfig, without changing any file.

    training_labels always remain null: this diagnostic API cannot certify a
    new solver run. A future publisher requires independently checked run IDs,
    geometry/config/result hashes, CAD readbacks and face-selection evidence.
    Merely placing a manifest with 'finished': true never releases labels.
    """
    given = Path(path)
    root = given.parent if given.is_file() else given
    if not root.is_dir():
        raise FileNotFoundError(root)
    configs = [given] if given.is_file() and given.suffix == ".xmlconfig" else sorted(root.glob("*.xmlconfig"))
    reasons, issues, goals = [], [], []
    report = {"schema_version": 1, "project_dir": str(root.resolve()),
              "mode": "read_only_audit", "training_labels": dict(_LABELS),
              "publishable": False, "surface_goals": goals,
              "blocking_reasons": reasons, "issues": issues}
    if len(configs) != 1:
        reasons.append("xmlconfig_missing_or_ambiguous")
        report["config_candidates"] = [str(x) for x in configs]
        return report
    cfg = configs[0]
    raw = _read(cfg)
    try:
        ET.fromstring(raw)
        report["whole_xml_valid"] = True
    except ET.ParseError as exc:
        report["whole_xml_valid"] = False
        report["whole_xml_parse_error"] = str(exc)
        issues.append("whole_xml_invalid; only Surface_Goal and GoalInfo subblocks parsed")
    info_path = cfg.with_suffix(".info.json")
    info = {}
    if info_path.is_file():
        try:
            info = json.loads(_read(info_path))
        except (ValueError, UnicodeError) as exc:
            issues.append(f"info_json_parse_failed:{exc}")
    report["solver"] = {"finished": info.get("finished"),
                        "telemetry": info.get("telemetry", {}),
                        "settings": info.get("settings", {}),
                        "config_name": info.get("config_name"),
                        "project_name": info.get("project_name")}
    if info.get("finished") is not True:
        reasons.append("solver_finished_not_true")
    if info.get("settings", {}).get("structural") is False:
        issues.append("structural_false: fluid pressure is not solid contact stress")
    runtime_goals = {}
    for item in info.get("goals", []):
        goal = item.get("goal", {})
        runtime_goals.setdefault(goal.get("name"), []).append(goal)
    goal_info = {}
    try:
        for node in _blocks(raw, "GoalInfo"):
            goal_info[_val(node, "GUID")] = {tag: _val(node, tag) for tag in
                                             ("GUID", "Goal_Type", "Parameter", "UnitId")}
        nodes = list(_blocks(raw, "Surface_Goal"))
    except ET.ParseError as exc:
        nodes = []
        reasons.append(f"goal_block_parse_failed:{exc}")
    for node in nodes:
        kind = int(_val(node, "Goal_Type", "-1"))
        semantic, unit, category = _TYPES.get(kind, ("unknown", None, None))
        name, guid = _val(node, "Name"), _val(node, "GUID")
        faces = node.findtext("Faces_Keys", "").split()
        item = {"name": name, "GoalID": guid, "Goal_Type": kind,
                "Goal_Calc_Value": int(_val(node, "Goal_Calc_Value", "-1")),
                "Faces_Keys": faces, "BodiesIDs": node.findtext("BodiesIDs", "").split(),
                "solver_goal_info": goal_info.get(guid), "physical_quantity": semantic,
                "expected_si_unit": unit, "expected_runtime_category": category,
                "unit_status": "expected_from_observed_type_and_SI_export; verify_for_new_export",
                "training_label": None,
                "selection_status": "face_ids_only; persistent_reference_validation_required"}
        if kind == 0 and ("密比压" in name or "法向" in name):
            item["name_warning"] = "Fluid pressure goal name does not make it sigma_n or q_calc"
        values = runtime_goals.get(name, [])
        if len(values) == 1:
            item["info_json"] = values[0]
            item["runtime_unit_category_matches"] = values[0].get("unit") == category
        elif values:
            issues.append(f"ambiguous_runtime_goal_name:{name}")
        # Goal text names are not trusted paths.
        dat_path = root / "Goals.DAT" / (name + ".txt")
        if dat_path.parent.resolve() == (root / "Goals.DAT").resolve() and dat_path.is_file():
            try:
                item["dat"] = _dat(dat_path)
                last = item["dat"]["last_row"]
                value = values[0].get("value") if len(values) == 1 else None
                item["info_minus_dat_last"] = _difference(value, last.get("Value"))
                item["info_minus_dat_average"] = _difference(value, last.get("AvValue"))
                if last.get("Progress", 0) < 100:
                    reasons.append(f"goal_not_converged:{guid}")
            except (ValueError, UnicodeError) as exc:
                issues.append(f"goal_history_parse_failed:{name}:{exc}")
        goals.append(item)
    report["raw_pressure_goals"] = [g["GoalID"] for g in goals if g["Goal_Type"] == 0]
    report["raw_flow_goals"] = [g["GoalID"] for g in goals if g["Goal_Type"] == 16]
    report["raw_moment_goals"] = [g["GoalID"] for g in goals if g["Goal_Type"] == 23]
    result_path = root / "project_results.xml"
    arms = []
    if result_path.is_file():
        try:
            for node in ET.fromstring(_read(result_path)).findall(".//Arm"):
                arm = {"name": node.findtext("Name"), "id": node.findtext("ID")}
                for tag in ("Area", "M", "P", "Rho", "T"):
                    value = node.findtext(tag)
                    arm[tag] = _number(value, tag) if value else None
                arms.append(arm)
        except (ET.ParseError, ValueError, UnicodeError) as exc:
            issues.append(f"project_results_parse_failed:{exc}")
    report["flowmaster_arms_raw"] = arms
    report["flowmaster_vs_surface_pressures"] = []
    for arm in arms:
        for goal in goals:
            if goal["Goal_Type"] == 0 and arm["id"] in goal["Faces_Keys"]:
                report["flowmaster_vs_surface_pressures"].append({
                    "arm_id": arm["id"], "GoalID": goal["GoalID"],
                    "flowmaster_P_minus_info_value": _difference(arm["P"], goal.get("info_json", {}).get("value")),
                    "note": "Different source/aggregation/time; do not mix automatically"})
    tracked = sorted({p for p in [cfg, info_path, result_path, cfg.with_suffix(".geom"),
                                  cfg.with_suffix(".fld"), cfg.with_suffix(".gdb"),
                                  root / "calculation_status.log"] if p.is_file()}
                     | set((root / "Goals.DAT").glob("SG*.txt")))
    report["files"] = [_record(p, root) for p in tracked]
    geometry = cfg.with_suffix(".geom")
    field = cfg.with_suffix(".fld")
    if not geometry.is_file():
        reasons.append("geometry_file_missing")
    if not field.is_file():
        reasons.append("field_result_missing")
    if geometry.is_file():
        for result in [field, result_path]:
            if result.is_file() and result.stat().st_mtime_ns < geometry.stat().st_mtime_ns:
                reasons.append(f"result_older_than_geometry:{result.name}")
    report["manifest_files_found"] = [p.name for p in root.glob("*manifest*.json")]
    reasons.extend(["verified_run_geometry_result_link_missing",
                    "face_selection_not_revalidated",
                    "audit_only_no_training_label_publication"])
    report["cv_policy"] = {
        "status": "not_calculated_from_mixed_legacy_sources",
        "required": ["Q_m3_s", "p_in_Pa", "p_out_Pa", "rho_kg_m3",
                     "same_run_id", "verified_goal_units_and_faces", "liquid_regime_checks"],
        "raw_goal_candidates_by_name": {g["name"]: g["GoalID"] for g in goals if "CV" in g["name"]},
        "equation": "Cv=(Q_m3_s*60/0.003785411784)*sqrt((rho/1000)/(delta_p_Pa/6894.757293168))",
        "reference_diameter_rule": "D_ref is pipe/reference flow diameter, never inferred from Dmax"}
    return report
