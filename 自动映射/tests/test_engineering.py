"""Independent cached workbook values and ingestion safety regression checks."""
import math
import unittest

from valve_mapping.engineering import (
    EngineeringInputError, required_sealing_pressure, sealing_pressure,
    torque_case, torque_peak,
)


def seat_sample():
    return {
        "P_MPa": 3.9, "b_M_mm": 7.5, "D_MN_mm": 94.0, "q_allow_MPa": 40.0,
        "material_branch": "seat_workbook_1p8_0p9",
    }


def torque_sample():
    # The spreadsheet's H23 is blank.  An explicit zero is supplied ONLY to
    # reproduce its cached subtotal, not to declare physical hydro torque zero.
    return {
        "case_id": "cached_subtotal_explicit_zero", "Dmax_mm": 191.32,
        "b_M_mm": 7.5, "P_MPa": 6.3, "material_branch": "solid_metal",
        "f_1": 0.3, "L_mm": 32.0, "F_G_N": 68.0, "ds_mm": 45.0,
        "mu_1": 0.15, "hydro_branch": "explicit", "M_h_Nmm": 0.0,
        "packing_branch": "graphite_or_asbestos_free", "d_T_mm": 45.0,
        "psi_1": 2.22, "b_T_mm": 10.0,
    }


class WorkbookValueTests(unittest.TestCase):
    def test_seat_cached_h_column_and_force_balance(self):
        out = sealing_pressure(seat_sample())
        self.assertAlmostEqual(out["q_MF_MPa"], 6.131459858793826, places=12)
        self.assertAlmostEqual(out["F_MJ_N"], 31555.405415625002, places=8)
        self.assertAlmostEqual(out["F_MF_N"], 14663.183147697613, places=8)
        self.assertAlmostEqual(out["F_MZ_N"], 46218.58856332261, places=8)
        self.assertAlmostEqual(out["q_calc_MPa"], 19.326459858793825, places=12)
        self.assertTrue(out["sealing_pressure_pass"])
        self.assertEqual(out["label_kind"], "q_calc")

    def test_cl150_l_column_independent_case(self):
        sample = seat_sample()
        sample.update(P_MPa=1.96, b_M_mm=8.0, D_MN_mm=191.0)
        out = sealing_pressure(sample)
        self.assertAlmostEqual(out["q_calc_MPa"], 16.173423135904624, places=12)
        self.assertAlmostEqual(out["F_MZ_N"], 80887.62358005944, places=7)

    def test_torque_cached_subtotal_and_components(self):
        out = torque_case(torque_sample())
        self.assertAlmostEqual(out["R_mm"], 74.82867126634413, places=11)
        self.assertAlmostEqual(out["F_m_N"], 15302.958424876333, places=8)
        self.assertAlmostEqual(out["M_m_Nmm"], 1145100.0453776026, places=7)
        self.assertAlmostEqual(out["M_C_Nmm"], 611178.1757072998, places=7)
        self.assertAlmostEqual(out["M_T_Nmm"], 141608.25, places=8)
        self.assertAlmostEqual(out["M_total_Nmm"], 1897886.4710849025, places=7)
        self.assertAlmostEqual(out["T_case_Nm"], 1897.8864710849025, places=9)
        self.assertFalse(out["actuator_multiplier_applied"])

    def test_three_torque_material_formulas(self):
        sample = torque_sample()
        expected = {
            "rubber_medium": 4.826648250425271,
            "multilayer": 8.07135676327097,
            "solid_metal": 11.316065276116667,
        }
        for branch, value in expected.items():
            with self.subTest(branch=branch):
                sample["material_branch"] = branch
                self.assertAlmostEqual(required_sealing_pressure(sample), value, places=12)

    def test_static_hydro_workbook_value_is_added(self):
        sample = torque_sample()
        sample.pop("M_h_Nmm")
        sample.update(hydro_branch="static", D1_mm=210.0, gamma_N_per_mm3=1e-5)
        out = torque_case(sample)
        self.assertAlmostEqual(out["M_h_Nmm"], 954.6282210937501, places=10)
        self.assertAlmostEqual(out["T_case_Nm"], 1898.8410993059963, places=9)

    def test_dynamic_units_and_signed_coefficient(self):
        sample = torque_sample()
        sample.update(hydro_branch="dynamic", m_alpha_1=-0.1, delta_p_MPa=0.5,
                      Dmax_mm=200.0)
        sample.pop("M_h_Nmm")
        out = torque_case(sample)
        self.assertAlmostEqual(out["M_h_Nmm"], -400000.0, places=6)

    def test_packing_branches_require_and_use_their_inputs(self):
        sample = torque_sample()
        sample.update(packing_branch="v_or_rectangular_ring", P_MPa=10.0,
                      d_T_mm=20.0, mu_T_1=0.1, Z_count=2, h_ring_mm=5.0)
        self.assertAlmostEqual(torque_case(sample)["M_T_Nmm"], 376.98, places=6)
        sample.update(packing_branch="o_ring", mu_O_1=0.2, d_O_mm=5.0)
        self.assertAlmostEqual(torque_case(sample)["M_T_Nmm"], 1906.0, places=6)

    def test_constants_mode_is_explicit_and_q_cancels_pi(self):
        q_old = sealing_pressure(seat_sample())
        q_math = sealing_pressure(seat_sample(), constants="mathematical")
        self.assertAlmostEqual(q_old["q_calc_MPa"], q_math["q_calc_MPa"], places=12)
        self.assertNotEqual(q_old["F_MJ_N"], q_math["F_MJ_N"])
        t_old = torque_case(torque_sample())
        t_math = torque_case(torque_sample(), constants="mathematical")
        self.assertNotEqual(t_old["M_C_Nmm"], t_math["M_C_Nmm"])
        self.assertEqual(t_math["constants_mode"], "mathematical")

    def test_allowable_limit_and_explicit_material(self):
        sample = seat_sample()
        sample["q_allow_MPa"] = 10.0
        self.assertFalse(sealing_pressure(sample)["sealing_pressure_pass"])
        sample.update(material_branch="explicit", q_MF_MPa=5.0,
                      q_MF_source="approved material calibration")
        self.assertEqual(required_sealing_pressure(sample), 5.0)


class InputAndCoverageTests(unittest.TestCase):
    def test_missing_hydro_does_not_become_zero(self):
        for missing in ("hydro_branch", "M_h_Nmm"):
            sample = torque_sample()
            sample.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(EngineeringInputError):
                torque_case(sample)

    def test_l_is_not_inferred_from_design_eccentricities(self):
        sample = torque_sample()
        sample.pop("L_mm")
        sample.update(c_mm=32.0, e_mm=10.0)
        with self.assertRaisesRegex(EngineeringInputError, "L_mm"):
            torque_case(sample)

    def test_seal_contact_width_is_not_sheet_thickness(self):
        sample = seat_sample()
        sample.pop("b_M_mm")
        sample["bm_mm"] = 7.5
        with self.assertRaisesRegex(EngineeringInputError, "b_M_mm"):
            sealing_pressure(sample)

    def test_missing_material_allowable_and_unit_suffix(self):
        for missing in ("material_branch", "q_allow_MPa", "D_MN_mm", "P_MPa"):
            sample = seat_sample()
            sample.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(EngineeringInputError):
                sealing_pressure(sample)

    def test_invalid_numeric_values_are_rejected(self):
        for value in (0.0, -1.0, float("nan"), float("inf"), True, "7.5"):
            sample = seat_sample()
            sample["b_M_mm"] = value
            with self.subTest(value=value), self.assertRaises(EngineeringInputError):
                sealing_pressure(sample)

    def test_unknown_modes_and_explicit_pressure_provenance(self):
        with self.assertRaises(EngineeringInputError):
            sealing_pressure(seat_sample(), constants="approximate")
        sample = seat_sample()
        sample.update(material_branch="unknown")
        with self.assertRaises(EngineeringInputError):
            required_sealing_pressure(sample)
        sample.update(material_branch="explicit", q_MF_MPa=5.0)
        with self.assertRaises(EngineeringInputError):
            required_sealing_pressure(sample)

    def test_no_fractional_packing_ring_count(self):
        sample = torque_sample()
        sample.update(packing_branch="v_or_rectangular_ring", mu_T_1=0.1,
                      Z_count=2.5, h_ring_mm=10.0)
        with self.assertRaisesRegex(EngineeringInputError, "Z_count"):
            torque_case(sample)

    def test_case_peak_requires_actual_protocol_coverage(self):
        first = torque_sample()
        first.update(case_id="opening_0", design_id="D001")
        second = dict(first, case_id="opening_45", M_h_Nmm=100000.0)
        result = torque_peak([first, second], "open_close_v1",
                             expected_case_ids=["opening_0", "opening_45"])
        self.assertEqual(result["governing_case_id"], "opening_45")
        self.assertEqual(result["coverage_status"], "declared_case_set_complete")
        self.assertFalse(result["full_cycle_coverage_asserted"])
        self.assertAlmostEqual(result["T_peak_Nm"], 1997.8864710849025, places=9)
        with self.assertRaisesRegex(EngineeringInputError, "expected_case_ids"):
            torque_peak([first], "open_close_v1",
                        expected_case_ids=["opening_0", "opening_45"])
        with self.assertRaisesRegex(EngineeringInputError, "duplicate case_id"):
            torque_peak([first, first], "open_close_v1")

    def test_single_case_does_not_claim_full_cycle(self):
        result = torque_peak([torque_sample()], "single_45")
        self.assertEqual(result["coverage_status"], "single_case")
        self.assertFalse(result["expected_case_set_verified"])
        self.assertFalse(result["full_cycle_coverage_asserted"])

    def test_case_set_cannot_mix_designs_or_materials(self):
        first = dict(torque_sample(), case_id="A", design_id="D001")
        second = dict(first, case_id="B", design_id="D002")
        with self.assertRaisesRegex(EngineeringInputError, "design_id"):
            torque_peak([first, second], "v1")
        second.update(design_id="D001", material_branch="multilayer")
        with self.assertRaisesRegex(EngineeringInputError, "material_branch"):
            torque_peak([first, second], "v1")

    def test_peak_absolute_convention_is_optional_and_recorded(self):
        first = dict(torque_sample(), case_id="positive")
        second = dict(first, case_id="negative", M_h_Nmm=-5e6)
        algebraic = torque_peak([first, second], "v1", peak_mode="workbook_max")
        absolute = torque_peak([first, second], "v1", peak_mode="max_abs")
        self.assertEqual(algebraic["governing_case_id"], "positive")
        self.assertEqual(absolute["governing_case_id"], "negative")
        self.assertGreater(absolute["T_peak_Nm"], algebraic["T_peak_Nm"])
        self.assertLess(absolute["governing_signed_torque_Nm"], 0.0)


if __name__ == "__main__":
    unittest.main()
