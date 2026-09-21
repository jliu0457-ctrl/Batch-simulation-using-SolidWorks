"""Import verified seven-variable CAD evidence without connecting to SolidWorks.

The copied bundle is an immutable evidence snapshot. Native assembly references
are preserved as saved; this module does not claim to have reopened or relinked
the copied assembly. No solver is run and no response/training label is created.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

from . import runs

SCHEMA = "seven_variable_cad_v1"
SIX_FIELDS = tuple(k for k in runs.INPUT_FIELDS if k != "Dmax_mm")
_GEOMETRY_PROTOCOL = {
    "protocol_id": SCHEMA,
    "length_tolerance_mm": 0.001,
    "angle_tolerance_deg": 1e-5,
    "persisted_dimension_tolerance_SI": 1e-8,
    "Dmax_interval_width_mm": 0.001,
    "Dmax_acceptance": "entire_interval_within_target_plus_minus_length_tolerance",
    "Dmax_method": "reopened_disc_projected_adaptive_caliper",
    "axis_tolerance_mm": 0.001,
    "axis_parallelism_tolerance": 1e-8,
    "expected_external_parts": 14,
    "expected_assemblies": 1,
    "fixed_opening_deg": 45,
    "fixed_endcap_parts": [f"封盖{i}.SLDPRT" for i in range(1, 9)],
}


def geometry_protocol():
    """Return the frozen, explicit protocol required in protocol['geometry']."""
    return copy.deepcopy(_GEOMETRY_PROTOCOL)


def _protocol(protocol):
    if not isinstance(protocol, dict) or protocol.get("geometry") != _GEOMETRY_PROTOCOL:
        raise ValueError("protocol.geometry must equal cad_bridge.geometry_protocol()")


def _finite(value, name):
    return runs._finite(value, name)


def _near(actual, expected, tolerance, name):
    if abs(_finite(actual, name) - _finite(expected, name)) > tolerance:
        raise ValueError(f"{name}: measurement differs beyond {tolerance}")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: nonempty text required")
    return value


def _name(value):
    value = _text(value, "CAD filename")
    if Path(value).name != value or any(c in value for c in "/\\:") or value in (".", ".."):
        raise ValueError("CAD filename must be a simple basename")
    return value


def _reject_errors(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if (key.lower() in ("error", "errors", "feature_errors") or
                    key.lower().endswith("_error")) and child not in (None, "", 0, False, []):
                raise ValueError(f"CAD report contains nonempty {key}")
            _reject_errors(child)
    elif isinstance(value, list):
        for child in value:
            _reject_errors(child)


def _result(document):
    if not isinstance(document, dict):
        raise ValueError("CAD report must be a JSON object")
    _reject_errors(document)
    if "execution" in document or "wrapper_version" in document:
        execution = document.get("execution")
        if (document.get("wrapper_version") not in {"single_design_v1", "single_design_v2"} or
                not isinstance(execution, dict) or not isinstance(execution.get("result"), dict) or
                "result" in document):
            raise ValueError("unknown or ambiguous single-design wrapper report")
        for field in ("completed", "physical_mapping_verified", "template_unchanged",
                      "cad_executed", "temporary_environment_restored"):
            if document.get(field) is not True:
                raise ValueError(f"wrapper {field} was not verified")
        if document.get("status") != "geometry_verified":
            raise ValueError("wrapper geometry status was not verified")
        if document.get("training_ready") is not False or document.get("simulation_labels_generated") is not False:
            raise ValueError("wrapper must remain geometry-only")
        if execution.get("command_in_progress_restored") is not True:
            raise ValueError("wrapper did not restore SolidWorks CommandInProgress")
        result = execution["result"]
        if runs.validate_design(document.get("input")) != runs.validate_design(result.get("input")):
            raise ValueError("wrapper input differs from executed CAD input")
        if document.get("clone_hashes_after") != _cad_hashes(result):
            raise ValueError("wrapper clone hashes differ from saved CAD report")
        before = document.get("template_hashes_before")
        if (not isinstance(before, dict) or set(before) != set(_cad_hashes(result)) or
                any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for h in before.values()) or
                document.get("template_hashes_after") != before or
                document.get("clone_hashes_before") != before):
            raise ValueError("wrapper unchanged-template and initial-clone hash evidence inconsistent")
    elif isinstance(document.get("result"), dict):
        result = document["result"]
    elif "input" in document:
        result = document
    else:
        raise ValueError("expected a direct/result/execution.result CAD report")
    if result.get("completed") is not True or result.get("physical_mapping_verified") is not True:
        raise ValueError("CAD mapping not completed and physically verified")
    if result.get("training_ready") is not False:
        raise ValueError("CAD-only report must explicitly retain training_ready=false")
    return result


def _cad_hashes(result):
    entries = result.get("cad_hashes_after")
    if not isinstance(entries, list) or len(entries) != 15:
        raise ValueError("exactly fifteen saved native CAD hashes required")
    out = {}
    for entry in entries:
        name = _name(entry["file"])
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("invalid saved CAD SHA256")
        if name.casefold() in {key.casefold() for key in out}:
            raise ValueError("duplicate CAD filename")
        out[name] = digest.lower()
    if (sum(Path(n).suffix.lower() == ".sldasm" for n in out) != 1 or
            sum(Path(n).suffix.lower() == ".sldprt" for n in out) != 14):
        raise ValueError("CAD bundle requires one assembly and fourteen external parts")
    caps = {f"封盖{i}.SLDPRT" for i in range(1, 9)}
    if not caps.issubset(out):
        raise ValueError("sealed-flow CAD bundle requires fixed endcaps 封盖1 through 封盖8")
    return out


def _match_six(values, design):
    if not isinstance(values, dict):
        raise ValueError("six reopened entity measurements required")
    out = {}
    for field in SIX_FIELDS:
        out[field] = _finite(values.get(field), field)
        tol = 1e-5 if field.endswith("_deg") else 0.001
        _near(out[field], design[field], tol, field)
    return out


def _axis(axis):
    if not isinstance(axis, dict):
        raise ValueError("assembly shaft/body axis evidence missing")
    offset = _finite(axis.get("axis_offset_mm"), "axis_offset_mm")
    dot = _finite(axis.get("axis_abs_dot"), "axis_abs_dot")
    if offset < 0 or dot < 0 or dot > 1 + 1e-8:
        raise ValueError("invalid assembly axis measurement")
    _near(offset, 0, 0.001, "axis_offset_mm")
    _near(dot, 1, 1e-8, "axis_abs_dot")


def _caliper(data, design):
    if not isinstance(data, dict):
        raise ValueError("reopened physical Dmax interval missing")
    lower, upper, midpoint, band = (
        _finite(data.get(k), k) for k in ("lower_mm", "upper_mm", "midpoint_mm", "band_mm"))
    if lower <= 0 or upper < lower or band < 0 or band > 0.001:
        raise ValueError("Dmax interval reversed, nonpositive, or wider than 0.001 mm")
    _near(band, upper - lower, 1e-8, "Dmax band")
    _near(midpoint, (lower + upper) / 2, 1e-8, "Dmax midpoint")
    target = design["Dmax_mm"]
    if max(abs(lower - target), abs(upper - target)) > 0.001:
        raise ValueError("entire measured Dmax interval must lie within target +/-0.001 mm")
    _text(data.get("plane"), "Dmax projection plane")
    _text(data.get("bound"), "Dmax bound method")
    if "Adaptive periodic angular intervals" not in data["bound"]:
        raise ValueError("unsupported Dmax bound method")
    samples = data.get("samples")
    if not isinstance(samples, list) or not 16 <= len(samples) <= 8192:
        raise ValueError("adaptive Dmax support samples missing")
    if data.get("directions") != len(samples):
        raise ValueError("Dmax direction count does not match samples")
    pairs = [(_finite(s.get("angle_deg"), "sample angle"),
              _finite(s.get("width_mm"), "sample width")) for s in samples]
    if any(not 0 <= a < 180 or w <= 0 for a, w in pairs):
        raise ValueError("invalid Dmax support sample")
    if pairs != sorted(pairs) or len({a for a, _ in pairs}) != len(pairs):
        raise ValueError("Dmax sample angles must be sorted and unique")
    gaps, bounds = [], []
    for i, (angle, width) in enumerate(pairs):
        next_angle, next_width = pairs[(i + 1) % len(pairs)]
        gap = next_angle - angle if i + 1 < len(pairs) else next_angle + 180 - angle
        if not 0 < gap < 180:
            raise ValueError("invalid periodic Dmax interval")
        gaps.append(gap)
        bounds.append(max(width, next_width) / math.cos(math.radians(gap) / 2))
    sampled_lower = max(w for _, w in pairs)
    computed_upper = max(sampled_lower, max(bounds)) + 1e-12 * max(1, sampled_lower)
    _near(lower, sampled_lower, 1e-8, "Dmax sampled lower bound")
    _near(upper, computed_upper, 1e-8, "Dmax adaptive upper bound")
    _near(data.get("step_deg"), max(gaps), 1e-8, "Dmax largest interval")
    return copy.deepcopy(data)


def _reference_check(result, hashes):
    source_folder = Path(_text(result.get("folder"), "source CAD folder")).resolve()
    parts = {name for name in hashes if Path(name).suffix.lower() == ".sldprt"}
    for key in ("references", "reopened_references"):
        refs = result.get(key)
        if not isinstance(refs, list):
            raise ValueError(f"{key}: assembly references missing")
        external = []
        for ref in refs:
            if ref.get("is_virtual") not in (True, False):
                raise ValueError("reference must explicitly identify virtual state")
            if ref["is_virtual"]:
                continue  # Embedded virtual components are bound by the assembly hash.
            path = Path(_text(ref.get("path"), "external component path")).resolve()
            if path.parent != source_folder:
                raise ValueError("reported external CAD reference escapes original trial folder")
            external.append(path.name)
        if len(external) != 14 or set(external) != parts:
            raise ValueError("all fourteen reopened external parts must match the saved CAD hashes")


def _controls(result, design, hashes):
    writes = result.get("writes")
    persisted = result.get("persisted_readback")
    if not isinstance(writes, list) or not writes or not isinstance(persisted, list):
        raise ValueError("dimension writes and saved/reopened readbacks required")
    last = {}
    for item in writes:
        key = (_name(item["file"]), _text(item.get("parameter"), "CAD parameter"))
        if key[0] not in hashes or Path(key[0]).suffix.lower() != ".sldprt":
            raise ValueError("dimension write references an unbound CAD part")
        if item.get("set_status") != 0 or isinstance(item.get("set_status"), bool):
            raise ValueError("CAD dimension write did not succeed")
        _text(item.get("variable"), "CAD variable")
        _finite(item.get("before_SI"), "dimension before_SI")
        _near(item.get("after_SI"), item.get("requested_SI"), 1e-8, "dimension write SI")
        last[key] = item
    saved = {}
    for item in persisted:
        key = (_name(item["file"]), _text(item.get("parameter"), "persisted CAD parameter"))
        if key in saved:
            raise ValueError("duplicate persisted CAD dimension")
        saved[key] = item
    if set(saved) != set(last):
        raise ValueError("persisted readbacks must cover every final dimension write")
    groups = {field: [] for field in SIX_FIELDS}
    normalized, construction = [], []
    for key, item in last.items():
        read = saved[key]
        actual = _finite(read.get("value_SI"), "persisted value_SI")
        expected = _finite(read.get("expected_SI"), "persisted expected_SI")
        _near(expected, item["requested_SI"], 1e-12, "persisted requested_SI provenance")
        _near(actual, expected, 1e-8, "persisted dimension SI")
        variable = item["variable"]
        field = "alpha_deg" if variable == "alpha_deg/2" else variable
        row = {"file": key[0], "parameter": key[1], "variable": variable,
               "value_SI": actual, "expected_SI": expected}
        if field in SIX_FIELDS:
            if field.endswith("_deg"):
                value = math.degrees(actual) * (2 if variable == "alpha_deg/2" else 1)
                formula = "degrees(value_SI)*2" if variable == "alpha_deg/2" else "degrees(value_SI)"
            else:
                value, formula = actual * 1000, "value_SI*1000"
            _near(value, design[field], 1e-5 if field.endswith("_deg") else 0.001, field)
            row.update(field=field, mapped_value=value, conversion=formula)
            groups[field].append(row)
        elif variable == "internal_s_mm" or variable.startswith("derived_"):
            construction.append(row)
        else:
            raise ValueError(f"unknown CAD variable mapping: {variable}")
        normalized.append(row)
    if any(not values for values in groups.values()):
        raise ValueError("saved dimension mapping must cover all six direct variables")
    internal = [row for row in construction if row["variable"] == "internal_s_mm"]
    if not internal:
        raise ValueError("Dmax inverse-solved construction controls missing")
    for row in internal:
        _near(row["value_SI"] * 1000, result.get("internal_s_mm"), 0.001, "internal_s_mm")
    readback = {field: values[0]["mapped_value"] for field, values in groups.items()}
    ids = {field: " ; ".join(f"{r['file']}::{r['parameter']}" for r in values)
           for field, values in groups.items()}
    ids["Dmax_mm"] = "inverse physical caliper via " + " ; ".join(
        f"{r['file']}::{r['parameter']}" for r in internal)
    return readback, ids, normalized, construction


def _normalize(document):
    result = _result(document)
    design = runs.validate_design(result.get("input"))
    hashes = _cad_hashes(result)
    _reference_check(result, hashes)
    if result.get("fixed_endcaps_verified") is not True or result.get("reopened_fixed_endcaps_verified") is not True:
        raise ValueError("all eight fixed endcaps must remain resolved before and after reopen")
    readback, parameter_ids, controls, construction = _controls(result, design, hashes)
    geometric = _match_six(result.get("analytic_geometry_after_reopen"), design)
    _text(result["analytic_geometry_after_reopen"].get("reference"), "entity measurement reference")
    interval = _caliper(result.get("caliper_after_reopen"), design)
    _axis(result.get("reopened_axis"))
    sweep = result.get("angle_sweep")
    if not isinstance(sweep, list) or len(sweep) != 1:
        raise ValueError("one fixed 45-degree assembly acceptance record required")
    angles = []
    for row in sweep:
        if row.get("completed") is not True or row.get("feature_errors") != []:
            raise ValueError("assembly opening acceptance incomplete or feature errors present")
        angle = _finite(row.get("requested_angle_deg"), "requested opening")
        _near(row.get("actual_angle_deg"), angle, 1e-5, "actual opening")
        _axis(row.get("geometry"))
        angles.append(angle)
    if angles != [45]:
        raise ValueError("assembly acceptance requires precisely the fixed 45-degree opening")
    trace = result.get("diameter_solve_trace")
    if not isinstance(trace, list) or not trace or any(r.get("completed") is not True for r in trace):
        raise ValueError("Dmax inverse-solve trace incomplete")
    _text(result.get("Dmax_definition"), "physical Dmax definition")
    return {
        "evidence_schema": SCHEMA, "status": "verified", "geometry_status": "verified",
        "rebuild_ok": True, "training_ready": False,
        "parameter_ids": parameter_ids, "cad_readback": readback,
        "geometry_measured": geometric, "geometry_intervals": {"Dmax_mm": interval},
        "control_readbacks": controls,
        "construction": {"internal_s_mm": result["internal_s_mm"], "controls": construction},
        "methods": {
            "cad_readback": "saved and reopened CAD dimensions; conversion retained per control",
            "geometry_measured": result["analytic_geometry_after_reopen"]["reference"],
            "Dmax_mm": result["Dmax_definition"],
            "rebuild_ok": "completed adapter plus fixed 45-degree feature/geometry acceptance",
            "kernel_precision": "reported angular bound excludes additional CAD kernel support error",
        },
        "tolerance": {k: 1e-5 if k.endswith("_deg") else 0.001 for k in runs.INPUT_FIELDS},
        "angle_sweep": copy.deepcopy(sweep), "reopened_axis": copy.deepcopy(result["reopened_axis"]),
        "reopened_references": copy.deepcopy(result["reopened_references"]),
        "cad_hashes_after": hashes,
    }


def _source(mapping_report_path, cad_folder=None):
    path = Path(mapping_report_path).resolve()
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8-sig"),
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"invalid JSON number {x}")))
    evidence = _normalize(document)
    result = _result(document)
    folder = Path(cad_folder).resolve() if cad_folder is not None else path.parent
    actual_names = {p.name for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in (".sldasm", ".sldprt")}
    if actual_names != set(evidence["cad_hashes_after"]):
        raise ValueError("source folder must contain exactly the reported fifteen native CAD files")
    for name, digest in evidence["cad_hashes_after"].items():
        file = runs._inside_file(folder, name)
        if file.stat().st_size == 0 or runs.file_hash(file) != digest:
            raise ValueError(f"source CAD file hash changed or empty: {name}")
    return path, folder, raw, document, evidence, runs.validate_design(result["input"])


def inspect_cad_mapping(mapping_report_path, cad_folder=None):
    """Read-only validation of a real report and all fifteen actual saved CAD files."""
    path, folder, raw, _, evidence, design = _source(mapping_report_path, cad_folder)
    return {"source_report": str(path), "source_cad_folder": str(folder),
            "source_report_sha256": hashlib.sha256(raw).hexdigest(),
            "design": design, "geometry_protocol": geometry_protocol(),
            "evidence": evidence, "training_ready": False}


def _write_json(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _record_source(run, source):
    run = runs._run_path(run)
    manifest = runs._manifest(run)
    _protocol(manifest["protocol"])
    if manifest.get("geometry") or manifest.get("mapping") or manifest["status"] != "created":
        raise ValueError("CAD import requires a fresh unregistered run")
    path, folder, raw, _, normalized, design = source
    if runs.canonical_hash(design) != manifest["design_hash"]:
        raise ValueError("CAD report design differs from the frozen run design")
    cad_dir, evidence_dir = run / "cad", run / "evidence"
    if cad_dir.exists() or evidence_dir.exists() or (run / "cad_bundle.json").exists():
        raise FileExistsError("CAD evidence destination already exists; never overwrite a partial import")
    cad_dir.mkdir()
    evidence_dir.mkdir()
    entries = []
    for name, digest in normalized["cad_hashes_after"].items():
        src = runs._inside_file(folder, name)
        dest = cad_dir / name
        with src.open("rb") as original, dest.open("xb") as copied:
            for chunk in iter(lambda: original.read(1048576), b""):
                copied.write(chunk)
        if runs.file_hash(dest) != digest or dest.stat().st_size == 0:
            raise ValueError(f"CAD changed while copying: {name}")
        entries.append({"path": dest.relative_to(run).as_posix(), "sha256": digest,
                        "role": "assembly" if dest.suffix.lower() == ".sldasm" else "external_part"})
    raw_path = evidence_dir / "mapping_result.json"
    with raw_path.open("xb") as stream:
        stream.write(raw)
    raw_spec = {"path": raw_path.relative_to(run).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}
    identity = {key: manifest[key] for key in ("run_id", "design_hash", "protocol_hash")}
    bundle = {**identity, "schema": "cad_bundle_v1", "files": entries, "source_report": raw_spec,
              "source_report_path": str(path), "source_cad_folder": str(folder),
              "reference_resolution": "source reopened references verified; copied assembly not reopened or relinked",
              "training_ready": False}
    bundle_path = run / "cad_bundle.json"
    _write_json(bundle_path, bundle)
    normalized.update(identity)
    normalized.update(raw_report=raw_spec, geometry_sha256=runs.file_hash(bundle_path))
    mapping_path = evidence_dir / "mapping_verified.json"
    _write_json(mapping_path, normalized)
    return runs.record_geometry(run, normalized["cad_readback"], bundle_path, mapping_path)


def record_cad_geometry(run, mapping_report_path, cad_folder=None):
    """Import into a freshly created run; preserve failed partial imports for audit."""
    return _record_source(run, _source(mapping_report_path, cad_folder))


def import_cad_geometry_run(base, design_id, mapping_report_path, protocol, *,
                            run_id=None, cad_folder=None):
    """Validate first, create an isolated run, copy/bind the fifteen-file evidence bundle.

    Return the new run Path. The run remains geometry_verified/training_ready=false.
    An existing run or evidence file is never overwritten.
    """
    _protocol(protocol)
    source = _source(mapping_report_path, cad_folder)
    run = runs.create_run(base, design_id, source[-1], protocol, run_id=run_id)
    _record_source(run, source)
    return run


def _check_registered_mapping(run, report, manifest, geometry_path):
    """Called by runs on registration, resume and publication; rehash every CAD part."""
    _protocol(manifest["protocol"])
    if report.get("training_ready") is not False:
        raise ValueError("CAD bridge must retain training_ready=false")
    raw_spec = report.get("raw_report")
    if not isinstance(raw_spec, dict) or set(raw_spec) != {"path", "sha256"}:
        raise ValueError("raw saved/reopened CAD report binding required")
    raw_path = runs._inside_file(run, raw_spec["path"])
    if runs.file_hash(raw_path) != raw_spec["sha256"]:
        raise ValueError("raw CAD report hash changed")
    document = runs._read_json(raw_path)
    expected = _normalize(document)
    if runs.canonical_hash(runs.validate_design(_result(document)["input"])) != manifest["design_hash"]:
        raise ValueError("raw CAD report design differs from run")
    expected.update({key: manifest[key] for key in ("run_id", "design_hash", "protocol_hash")})
    expected.update(raw_report=raw_spec, geometry_sha256=runs.file_hash(geometry_path))
    if report != expected:
        raise ValueError("normalized CAD mapping differs from original saved/reopened report")
    bundle = runs._read_json(geometry_path)
    runs._identity(bundle, manifest)
    if bundle.get("schema") != "cad_bundle_v1" or bundle.get("training_ready") is not False:
        raise ValueError("verified fifteen-file CAD bundle manifest required")
    if bundle.get("source_report") != raw_spec:
        raise ValueError("CAD bundle/raw report binding mismatch")
    files = bundle.get("files")
    if not isinstance(files, list) or len(files) != 15:
        raise ValueError("CAD bundle must bind all fifteen files")
    observed = {}
    for entry in files:
        path = runs._inside_file(run, entry.get("path", ""))
        if path.parent != run / "cad":
            raise ValueError("bundle CAD files must remain in the run cad directory")
        if path.name.casefold() in {name.casefold() for name in observed}:
            raise ValueError("duplicate CAD bundle file")
        expected_role = "assembly" if path.suffix.lower() == ".sldasm" else "external_part"
        if entry.get("role") != expected_role:
            raise ValueError("CAD bundle role mismatch")
        digest = runs.file_hash(path)
        if digest != entry.get("sha256") or path.stat().st_size == 0:
            raise ValueError(f"CAD bundle file hash changed or empty: {path.name}")
        observed[path.name] = digest
    if observed != expected["cad_hashes_after"]:
        raise ValueError("fifteen-file CAD bundle differs from accepted geometry report")
    actual_names = {p.name for p in (run / "cad").iterdir()
                    if p.is_file() and p.suffix.lower() in (".sldasm", ".sldprt")}
    if actual_names != set(observed):
        raise ValueError("unbound native CAD file present in run bundle")
    return copy.deepcopy(expected["cad_readback"])
