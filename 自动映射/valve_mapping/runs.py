"""Isolated design runs and evidence-gated training-row assembly.

No function runs CAD or a solver. Only an adapter with real readbacks/results
may create verified evidence. Boolean flags alone never satisfy publication.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

TASK_ROOT = Path(__file__).resolve().parents[1]
INPUT_FIELDS = ("c_mm", "e_mm", "phi_deg", "alpha_deg", "Dmax_mm", "bm_mm", "ds_mm")
LABEL_UNITS = {"T_peak_Nm": "N*m", "sigma_n_MPa": "MPa",
               "q_calc_MPa": "MPa", "Cv": "US_Cv"}
LABEL_SOURCES = {"T_peak_Nm": "engineering_total_torque",
                 "sigma_n_MPa": "solid_contact",
                 "q_calc_MPa": "engineering_sealing_pressure", "Cv": "liquid_flow"}
DEFAULT_TOLERANCE = {field: 1e-6 for field in INPUT_FIELDS}
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}

def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name}: bool is not a measurement")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: finite numeric value required")
    return float(value)

def validate_design(design):
    if not isinstance(design, dict) or set(design) != set(INPUT_FIELDS):
        raise ValueError("exactly seven explicitly unit-tagged design fields required")
    out = {k: _finite(design[k], k) for k in INPUT_FIELDS}
    if any(out[k] < 0 for k in ("c_mm", "e_mm", "phi_deg")):
        raise ValueError("eccentric distances and phi must be nonnegative")
    if any(out[k] <= 0 for k in ("Dmax_mm", "bm_mm", "ds_mm")):
        raise ValueError("diameter and thickness must be positive")
    if not 0 < out["alpha_deg"] < 180 or out["phi_deg"] >= 180:
        raise ValueError("angle outside cone parameter domain")
    return out

def canonical_hash(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1048576), b""):
            h.update(data)
    return h.hexdigest()

def _slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("design_id/run_id must be safe ASCII identifier")
    if value.upper() in _RESERVED:
        raise ValueError("reserved Windows path name")
    return value

def _within(path, base):
    resolved, base = Path(path).resolve(), Path(base).resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise ValueError("path must stay strictly inside its allowed root")
    return resolved

def _run_path(path):
    return _within(path, TASK_ROOT)

def _inside_file(run, path):
    path = Path(path)
    if not path.is_absolute():
        path = run / path
    result = _within(path, run)
    if not result.is_file():
        raise ValueError(f"evidence file missing: {result}")
    return result

def _read_json(path):
    def reject_constant(value):
        raise ValueError(f"invalid JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      parse_constant=reject_constant)

def _save_manifest(run, data):
    # Replacement applies only to this run's manifest; evidence never overwritten.
    dest = run / "manifest.json"
    tmp = run / ("manifest-" + uuid.uuid4().hex + ".tmp")
    with tmp.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
    tmp.replace(dest)

def _manifest(run):
    run = _run_path(run)
    data = _read_json(_inside_file(run, "manifest.json"))
    if data.get("schema_version") != 1:
        raise ValueError("unknown manifest schema")
    if data.get("run_id") != run.name or data.get("design_id") != run.parent.name:
        raise ValueError("manifest path/run identity mismatch")
    design = validate_design(data.get("design"))
    if canonical_hash(design) != data.get("design_hash"):
        raise ValueError("design hash mismatch")
    if canonical_hash(data.get("protocol")) != data.get("protocol_hash"):
        raise ValueError("protocol hash mismatch")
    return data

def create_run(base, design_id, design, protocol, *, run_id=None):
    """Create a fresh run under TASK_ROOT; existing runs are never overwritten."""
    base = _within(base, TASK_ROOT)
    design_id = _slug(design_id)
    run_id = _slug(run_id or ("run_" + uuid.uuid4().hex))
    design = validate_design(design)
    if not isinstance(protocol, dict) or not protocol.get("protocol_id"):
        raise ValueError("protocol must include protocol_id and frozen settings")
    protocol_hash = canonical_hash(protocol)
    run = _within(base / design_id / run_id, base)
    run.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "design_id": design_id, "run_id": run_id,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "status": "created", "design": design, "design_hash": canonical_hash(design),
                "protocol": protocol, "protocol_hash": protocol_hash,
                "mapping": None, "geometry": None, "cad_readback": None,
                "training_ready": False}
    _save_manifest(run, manifest)
    return run

def resume_run(run, design, protocol):
    """Only resume exactly matching design and protocol; check existing evidence."""
    run = _run_path(run)
    manifest = _manifest(run)
    if canonical_hash(validate_design(design)) != manifest["design_hash"]:
        raise ValueError("resume design differs")
    if canonical_hash(protocol) != manifest["protocol_hash"]:
        raise ValueError("resume protocol differs")
    if manifest.get("geometry") or manifest.get("mapping"):
        _check_geometry(run, manifest)
    return manifest

def _identity(data, manifest):
    for key in ("run_id", "design_hash", "protocol_hash"):
        if data.get(key) != manifest[key]:
            raise ValueError(f"evidence {key} mismatch")

def _measured_match(actual, design, tolerance):
    actual = validate_design(actual)
    for key in INPUT_FIELDS:
        tol = _finite(tolerance.get(key, DEFAULT_TOLERANCE[key]), f"{key} tolerance")
        # Prevent an arbitrary large tolerance from approving an unbound dimension.
        max_tol = max(1e-5, abs(design[key]) * 1e-5)
        if tol < 0 or tol > max_tol:
            raise ValueError(f"{key}: unsupported measurement tolerance")
        if abs(actual[key] - design[key]) > tol:
            raise ValueError(f"{key}: actual value does not match requested design")
    return actual

def _check_mapping(report, manifest, run=None, geometry_path=None):
    _identity(report, manifest)
    if report.get("evidence_schema") == "seven_variable_cad_v1":
        if run is None or geometry_path is None:
            raise ValueError("CAD bundle validation requires the isolated run and bundle path")
        from .cad_bridge import _check_registered_mapping
        return _check_registered_mapping(run, report, manifest, geometry_path)
    if manifest["protocol"].get("geometry", {}).get("protocol_id") == "seven_variable_cad_v1":
        raise ValueError("seven-variable CAD protocol requires full bundle evidence schema")
    if report.get("status") != "verified":
        raise ValueError("mapping not verified")
    if set(report.get("parameter_ids", {})) != set(INPUT_FIELDS):
        raise ValueError("seven real CAD parameter identifiers required")
    if any(not isinstance(v, str) or not v.strip() for v in report["parameter_ids"].values()):
        raise ValueError("empty real CAD parameter identifier")
    if report.get("rebuild_ok") is not True:
        raise ValueError("CAD rebuild did not pass")
    tolerance = report.get("tolerance", DEFAULT_TOLERANCE)
    _measured_match(report.get("cad_readback"), manifest["design"], tolerance)
    _measured_match(report.get("geometry_measured"), manifest["design"], tolerance)
    if report.get("geometry_status") != "verified":
        raise ValueError("geometry measurement not verified")

def record_geometry(run, cad_readback, geometry_path, mapping_report_path):
    """Bind real CAD readbacks, independent geometric measurements and file hashes.

    mapping_report JSON requires run_id/design_hash/protocol_hash, status verified,
    seven parameter_ids, rebuild_ok, cad_readback, geometry_measured and
    geometry_status verified. The seven_variable_cad_v1 evidence schema instead
    retains six readbacks plus a bounded physical Dmax interval and validates the
    complete fifteen-file sealed-flow CAD bundle. This function never invents the report.
    """
    run = _run_path(run)
    manifest = _manifest(run)
    geometry_path = _inside_file(run, geometry_path)
    mapping_path = _inside_file(run, mapping_report_path)
    report = _read_json(mapping_path)
    checked = _check_mapping(report, manifest, run, geometry_path)
    if report.get("evidence_schema") == "seven_variable_cad_v1":
        actual = checked  # Six scalar controls; Dmax remains a measured interval.
        if cad_readback != actual:
            raise ValueError("adapter readback disagrees with mapping evidence")
    else:
        actual = _measured_match(cad_readback, manifest["design"], report.get("tolerance", DEFAULT_TOLERANCE))
        if actual != validate_design(report["cad_readback"]):
            raise ValueError("adapter readback disagrees with mapping evidence")
    digest = file_hash(geometry_path)
    if report.get("geometry_sha256") != digest:
        raise ValueError("mapping references a different geometry")
    if geometry_path.stat().st_size == 0:
        raise ValueError("empty geometry file")
    manifest["cad_readback"] = actual
    manifest["mapping"] = {"path": mapping_path.relative_to(run).as_posix(),
                           "sha256": file_hash(mapping_path), "status": "verified"}
    manifest["geometry"] = {"path": geometry_path.relative_to(run).as_posix(),
                            "sha256": digest, "status": "verified",
                            "mtime_ns": geometry_path.stat().st_mtime_ns}
    manifest["status"] = "geometry_verified"
    manifest["training_ready"] = False
    if report.get("evidence_schema") == "seven_variable_cad_v1":
        manifest["geometry"]["kind"] = "cad_bundle_v1"
        manifest["geometry_intervals"] = report["geometry_intervals"]
        manifest["geometry_measured"] = report["geometry_measured"]
    _save_manifest(run, manifest)
    return manifest

def _check_geometry(run, manifest):
    geometry = manifest.get("geometry") or {}
    mapping = manifest.get("mapping") or {}
    if geometry.get("status") != "verified" or mapping.get("status") != "verified":
        raise ValueError("mapping_and_geometry_not_verified")
    gp = _inside_file(run, geometry.get("path", ""))
    mp = _inside_file(run, mapping.get("path", ""))
    if file_hash(gp) != geometry.get("sha256") or file_hash(mp) != mapping.get("sha256"):
        raise ValueError("geometry_or_mapping_hash_changed")
    report = _read_json(mp)
    checked = _check_mapping(report, manifest, run, gp)
    if report.get("geometry_sha256") != geometry["sha256"]:
        raise ValueError("mapping_geometry_hash_mismatch")
    if report.get("evidence_schema") == "seven_variable_cad_v1":
        if (manifest.get("cad_readback") != checked or
                manifest.get("geometry_intervals") != report["geometry_intervals"] or
                manifest.get("geometry_measured") != report["geometry_measured"] or
                manifest.get("training_ready") is not False or
                geometry.get("kind") != "cad_bundle_v1"):
            raise ValueError("manifest_CAD_geometry_evidence_mismatch")
    elif validate_design(manifest.get("cad_readback")) != validate_design(report["cad_readback"]):
        raise ValueError("manifest_cad_readback_mismatch")
    return gp

def _file_evidence(run, spec, manifest, geometry, *, require_solver_binding=False):
    if not isinstance(spec, dict) or set(spec) != {"path", "sha256"}:
        raise ValueError("evidence needs explicit path and sha256")
    path = _inside_file(run, spec["path"])
    if file_hash(path) != spec["sha256"]:
        raise ValueError("evidence file hash mismatch")
    data = _read_json(path)
    _identity(data, manifest)
    if data.get("geometry_sha256") != manifest["geometry"]["sha256"]:
        raise ValueError("result geometry hash mismatch")
    if path.stat().st_mtime_ns < geometry.stat().st_mtime_ns:
        raise ValueError("stale evidence file")
    if data.get("status") != "verified":
        raise ValueError("evidence not verified")
    if require_solver_binding and not data.get("source_run_id"):
        raise ValueError("solver/source run id missing")
    return data

def publish_training_row(run, label_evidence, quality_evidence):
    """Return a complete row only after evidence validation; never fill missing/NaN.

    label_evidence maps all four labels to {path, sha256}. Each JSON is from this
    run, geometry and protocol, with label/value/unit/source_type/source_run_id.
    quality JSON requires all named checks to equal "passed". It must be linked
    to the same run and geometry. This API returns a row; it does not write a CSV.
    """
    out = {"publishable": False, "row": None,
           "labels": {k: None for k in LABEL_UNITS}, "issues": []}
    try:
        run = _run_path(run)
        manifest = _manifest(run)
        geometry = _check_geometry(run, manifest)
        if not isinstance(label_evidence, dict) or set(label_evidence) != set(LABEL_UNITS):
            raise ValueError("all_four_label_evidence_required")
        quality = _file_evidence(run, quality_evidence, manifest, geometry)
        checks = quality.get("checks", {})
        required = ("geometry_valid", "interference", "face_selection",
                    "flow_finished", "flow_convergence", "flow_mesh",
                    "contact_finished", "contact_convergence", "contact_mesh",
                    "engineering_inputs", "liquid_cv_conditions")
        if any(checks.get(key) != "passed" for key in required):
            raise ValueError("quality_checks_missing_or_failed")
        values = {}
        source_runs = quality.get("source_run_ids", {})
        for name, unit in LABEL_UNITS.items():
            evidence = _file_evidence(run, label_evidence[name], manifest, geometry,
                                      require_solver_binding=True)
            if evidence.get("label") != name or evidence.get("unit") != unit:
                raise ValueError(f"{name}: wrong label or unit")
            if evidence.get("source_type") != LABEL_SOURCES[name]:
                raise ValueError(f"{name}: wrong physical source")
            raw_results = evidence.get("raw_results")
            if not isinstance(raw_results, list) or not raw_results:
                raise ValueError(f"{name}: raw result evidence missing")
            for raw_spec in raw_results:
                if not isinstance(raw_spec, dict) or set(raw_spec) != {"path", "sha256"}:
                    raise ValueError(f"{name}: raw result needs path and sha256")
                raw_path = _inside_file(run, raw_spec["path"])
                if file_hash(raw_path) != raw_spec["sha256"] or raw_path.stat().st_size == 0:
                    raise ValueError(f"{name}: raw result changed or empty")
                if raw_path.stat().st_mtime_ns < geometry.stat().st_mtime_ns:
                    raise ValueError(f"{name}: stale raw result")
            if source_runs.get(name) != evidence["source_run_id"]:
                raise ValueError(f"{name}: source run differs from quality evidence")
            value = _finite(evidence.get("value"), name)
            if value < 0 or (name == "Cv" and value <= 0):
                raise ValueError(f"{name}: negative response or nonpositive Cv")
            bounds = manifest["protocol"].get("label_bounds", {}).get(name)
            if bounds:
                lo, hi = (_finite(bounds[k], f"{name} {k}") for k in ("min", "max"))
                if lo > hi or not lo <= value <= hi:
                    raise ValueError(f"{name}: outside protocol limits")
            values[name] = value
        out.update(publishable=True, labels=values,
                   row={"design_id": manifest["design_id"], "run_id": manifest["run_id"],
                        "protocol_hash": manifest["protocol_hash"],
                        "geometry_sha256": manifest["geometry"]["sha256"],
                        **manifest["design"], **values})
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        out["issues"].append(str(exc))
    return out
