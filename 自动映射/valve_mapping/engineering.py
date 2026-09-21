"""Workbook-derived engineering labels with explicit units and branch selection.

These calculations do not produce finite-element contact stress.  No CAD radius,
eccentric distance, seal width, material choice, or missing hydro torque is inferred.
Public dictionaries contain Python primitives and can be serialized as JSON.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real

FORMULA_VERSION = "workbook_engineering_v1"
MATERIAL_BRANCHES = (
    "seat_workbook_1p8_0p9", "rubber_medium", "multilayer", "solid_metal", "explicit"
)
CONSTANTS = {
    "workbook": {"pi": 3.1415, "bearing_area_factor": 0.785, "arm_factor": 0.7071},
    "mathematical": {
        "pi": math.pi,
        "bearing_area_factor": math.pi / 4.0,
        "arm_factor": math.sqrt(0.5),
    },
}


class EngineeringInputError(ValueError):
    """A physical input, unit, or material/operating branch is missing or invalid."""


def _mapping(params):
    if not isinstance(params, Mapping):
        raise EngineeringInputError("params must be a mapping with explicit unit-suffixed keys")
    return params


def _number(params, key, *, positive=False, nonnegative=False):
    if key not in params:
        raise EngineeringInputError("missing required parameter: " + key)
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise EngineeringInputError(key + " must be a finite numeric value in the named unit")
    value = float(value)
    if not math.isfinite(value):
        raise EngineeringInputError(key + " must be finite")
    if positive and value <= 0:
        raise EngineeringInputError(key + " must be > 0")
    if nonnegative and value < 0:
        raise EngineeringInputError(key + " must be >= 0")
    return value


def _text(params, key):
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EngineeringInputError("missing nonempty string parameter: " + key)
    return value.strip()


def _constants(name):
    if name not in CONSTANTS:
        raise EngineeringInputError("constants must be 'workbook' or 'mathematical'")
    return CONSTANTS[name]


def required_sealing_pressure(params):
    """Return required seating pressure q_MF [MPa] for the chosen material formula.

    Required: P_MPa, b_M_mm, material_branch.  The branch 'explicit' additionally
    needs q_MF_MPa and q_MF_source. b_M is the contact width, not sheet thickness b_m.
    The first branch names the workbook formula, not an unverified material.
    """
    _mapping(params)
    pressure = _number(params, "P_MPa", nonnegative=True)
    width = _number(params, "b_M_mm", positive=True)
    branch = _text(params, "material_branch")
    root_width = math.sqrt(width / 10.0)
    if branch == "seat_workbook_1p8_0p9":
        return (1.8 + 0.9 * pressure) / root_width
    if branch == "rubber_medium":
        return (0.4 + 0.6 * pressure) / root_width
    if branch == "solid_metal":
        return (3.5 + pressure) / root_width
    if branch == "multilayer":
        return ((0.4 + 0.6 * pressure) + (3.5 + pressure)) / (2.0 * root_width)
    if branch == "explicit":
        _text(params, "q_MF_source")
        return _number(params, "q_MF_MPa", positive=True)
    raise EngineeringInputError("unknown material_branch: " + branch)


def sealing_pressure(params, constants="workbook"):
    """Return workbook seal pressure and its nominal-load components.

    Additional required inputs: D_MN_mm, q_allow_MPa.  Allowable pressure is
    explicit because the observed books use different material configurations.
    Output q_calc_MPa is NOT FE contact pressure or a CFD surface-pressure goal.
    """
    _mapping(params)
    coeff = _constants(constants)
    pressure = _number(params, "P_MPa", nonnegative=True)
    width = _number(params, "b_M_mm", positive=True)
    inner_diameter = _number(params, "D_MN_mm", positive=True)
    allowable = _number(params, "q_allow_MPa", positive=True)
    q_mf = required_sealing_pressure(params)
    mean_diameter = inner_diameter + width
    area = coeff["pi"] * mean_diameter * width
    fluid_force = coeff["pi"] / 4.0 * mean_diameter ** 2 * pressure
    seat_force = area * q_mf
    total_force = fluid_force + seat_force
    q_calc = total_force / area
    result = {
        "q_calc_MPa": q_calc,
        "q_MF_MPa": q_mf,
        "q_allow_MPa": allowable,
        "F_MJ_N": fluid_force,
        "F_MF_N": seat_force,
        "F_MZ_N": total_force,
        "nominal_seal_area_mm2": area,
        "sealing_pressure_pass": q_mf <= q_calc <= allowable,
        "lower_margin_MPa": q_calc - q_mf,
        "upper_margin_MPa": allowable - q_calc,
        "material_branch": params["material_branch"],
        "label_kind": "q_calc",
        "label_source": "2-阀座密封比压.xls:FMJ+FMF;material_branch_explicit",
        "formula_version": FORMULA_VERSION,
        "constants_mode": constants,
    }
    if params["material_branch"] == "explicit":
        result["q_MF_source"] = params["q_MF_source"]
    return result


def _hydro_torque(params, coeff, diameter):
    branch = _text(params, "hydro_branch")
    if branch == "explicit":
        # An intentional zero is allowed, but only as an explicit, traceable input.
        return _number(params, "M_h_Nmm"), "explicit"
    if branch == "static":
        d1 = _number(params, "D1_mm", positive=True)
        gamma = _number(params, "gamma_N_per_mm3", nonnegative=True)
        return coeff["pi"] * d1 ** 4 * gamma / 64.0, "static"
    if branch == "dynamic":
        m_alpha = _number(params, "m_alpha_1")
        delta_p = _number(params, "delta_p_MPa")
        return m_alpha * delta_p * diameter ** 3, "dynamic"
    raise EngineeringInputError("hydro_branch must be explicit, static, or dynamic")


def _packing_torque(params, coeff, pressure):
    branch = _text(params, "packing_branch")
    diameter = _number(params, "d_T_mm", positive=True)
    if branch == "graphite_or_asbestos_free":
        psi = _number(params, "psi_1", nonnegative=True)
        width = _number(params, "b_T_mm", positive=True)
        return 0.5 * psi * diameter ** 2 * width * pressure, branch
    if branch == "v_or_rectangular_ring":
        mu = _number(params, "mu_T_1", nonnegative=True)
        rings = _number(params, "Z_count", positive=True)
        if not rings.is_integer():
            raise EngineeringInputError("Z_count must be a positive integer")
        height = _number(params, "h_ring_mm", positive=True)
        return 0.6 * coeff["pi"] * mu * diameter * rings * height * pressure, branch
    if branch == "o_ring":
        mu = _number(params, "mu_O_1", nonnegative=True)
        section = _number(params, "d_O_mm", positive=True)
        return 0.5 * diameter ** 2 * (0.33 + 0.92 * mu * section * pressure), branch
    raise EngineeringInputError("unknown packing_branch: " + branch)


def torque_case(params, constants="workbook"):
    """Return one operating case's signed algebraic workbook torque.

    Required: case_id, Dmax_mm, b_M_mm, P_MPa, material_branch, f_1, L_mm,
    F_G_N, ds_mm, mu_1, hydro_branch, packing_branch, d_T_mm, and branch fields.
    L_mm is the workbook lever-arm eccentricity; it is NOT inferred from c/e.
    d_T_mm is explicit even when it happens to equal ds_mm.
    The result has no actuator sizing multiplier and is not a full-cycle peak.
    """
    _mapping(params)
    coeff = _constants(constants)
    case_id = _text(params, "case_id")
    diameter = _number(params, "Dmax_mm", positive=True)
    width = _number(params, "b_M_mm", positive=True)
    pressure = _number(params, "P_MPa", nonnegative=True)
    friction = _number(params, "f_1", nonnegative=True)
    eccentric = _number(params, "L_mm")  # signed location allowed; squared in R
    weight = _number(params, "F_G_N", nonnegative=True)
    shaft_diameter = _number(params, "ds_mm", positive=True)
    bearing_friction = _number(params, "mu_1", nonnegative=True)
    q_mf = required_sealing_pressure(params)
    radius = diameter / 2.0
    arm = math.sqrt((coeff["arm_factor"] * radius) ** 2 + eccentric ** 2)
    friction_force = coeff["pi"] * diameter * width * q_mf * friction
    seal_torque = friction_force * arm
    hydro_torque, hydro_branch = _hydro_torque(params, coeff, diameter)
    bearing_torque = (
        coeff["bearing_area_factor"] * diameter ** 2 * pressure + weight
    ) * shaft_diameter * bearing_friction / 2.0
    packing_torque, packing_branch = _packing_torque(params, coeff, pressure)
    total = seal_torque + hydro_torque + bearing_torque + packing_torque
    result = {
        "case_id": case_id,
        "T_case_Nm": total / 1000.0,
        "M_total_Nmm": total,
        "M_m_Nmm": seal_torque,
        "M_h_Nmm": hydro_torque,
        "M_C_Nmm": bearing_torque,
        "M_T_Nmm": packing_torque,
        "F_m_N": friction_force,
        "R_mm": arm,
        "q_MF_MPa": q_mf,
        "material_branch": params["material_branch"],
        "hydro_branch": hydro_branch,
        "packing_branch": packing_branch,
        "label_kind": "T_case",
        "label_source": "4-三偏心蝶阀扭矩.xls:Mm+Mh+MC+MT",
        "formula_version": FORMULA_VERSION,
        "constants_mode": constants,
        "actuator_multiplier_applied": False,
        "torque_convention": "algebraic_workbook_sum",
    }
    if "design_id" in params:
        result["design_id"] = _text(params, "design_id")
    if params["material_branch"] == "explicit":
        result["q_MF_source"] = params["q_MF_source"]
    return result


def torque_peak(cases, case_set_id, *, constants="workbook",
                peak_mode="workbook_max", expected_case_ids=None):
    """Aggregate explicitly supplied operating cases, without claiming cycle coverage.

    cases: sequence of torque_case INPUT mappings, each with a unique case_id.
    case_set_id: caller's stable protocol identifier.
    expected_case_ids: optional complete protocol set; when supplied it is enforced.
    peak_mode='workbook_max' computes max(M)/1000; 'max_abs' computes max(abs(M))/1000.
    A single case is permitted but reported as single_case, not a demonstrated cycle peak.
    """
    if not isinstance(case_set_id, str) or not case_set_id.strip():
        raise EngineeringInputError("case_set_id must be a nonempty protocol identifier")
    if isinstance(cases, (str, bytes)) or not isinstance(cases, Sequence) or not cases:
        raise EngineeringInputError("cases must be a nonempty sequence of input mappings")
    if peak_mode not in ("workbook_max", "max_abs"):
        raise EngineeringInputError("peak_mode must be workbook_max or max_abs")
    results = [torque_case(case, constants=constants) for case in cases]
    case_ids = [row["case_id"] for row in results]
    if len(set(case_ids)) != len(case_ids):
        raise EngineeringInputError("duplicate case_id in case set")
    design_ids = [row.get("design_id") for row in results]
    if any(item is not None for item in design_ids) and len(set(design_ids)) != 1:
        raise EngineeringInputError("case set mixes design_id values or omits some design_id values")
    material_branches = {row["material_branch"] for row in results}
    if len(material_branches) != 1:
        raise EngineeringInputError("one design case set must use one material_branch")
    coverage_verified = False
    if expected_case_ids is not None:
        if (isinstance(expected_case_ids, (str, bytes)) or
                not isinstance(expected_case_ids, Sequence) or not expected_case_ids or
                any(not isinstance(item, str) or not item.strip()
                    for item in expected_case_ids)):
            raise EngineeringInputError("expected_case_ids must be a nonempty string sequence")
        if len(set(expected_case_ids)) != len(expected_case_ids):
            raise EngineeringInputError("expected_case_ids contains duplicates")
        if set(case_ids) != set(expected_case_ids):
            raise EngineeringInputError("actual case_ids do not match expected_case_ids")
        coverage_verified = True
    score = ((lambda row: abs(row["T_case_Nm"])) if peak_mode == "max_abs"
             else (lambda row: row["T_case_Nm"]))
    governing = max(results, key=score)
    out = {
        "T_peak_Nm": score(governing),
        "governing_case_id": governing["case_id"],
        "governing_signed_torque_Nm": governing["T_case_Nm"],
        "case_set_id": case_set_id.strip(),
        "case_count": len(results),
        "case_ids": case_ids,
        "peak_mode": peak_mode,
        "coverage_status": (
            "single_case" if len(results) == 1 else
            "declared_case_set_complete" if coverage_verified else
            "provided_cases_only"
        ),
        "expected_case_set_verified": coverage_verified,
        "full_cycle_coverage_asserted": False,
        "material_branch": next(iter(material_branches)),
        "constants_mode": constants,
        "formula_version": FORMULA_VERSION,
        "cases": results,
    }
    if design_ids[0] is not None:
        out["design_id"] = design_ids[0]
    return out
