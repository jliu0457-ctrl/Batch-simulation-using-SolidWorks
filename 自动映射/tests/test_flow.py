import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from valve_mapping.flow import (cv_from_si, zeta_from_si, zeta_from_cv,
                                inspect_flow_project, US_GALLON_M3, PSI_PA)

class CvTests(unittest.TestCase):
    def test_standard_definition_and_units(self):
        self.assertAlmostEqual(cv_from_si(US_GALLON_M3 / 60, PSI_PA, 1000), 1.0)
        self.assertAlmostEqual(cv_from_si(US_GALLON_M3 / 60, PSI_PA, 4000), 2.0)

    def test_cv_and_zeta_equivalence(self):
        q, dp, rho, diameter = 0.1, 41000, 997.56, 210.0
        cv = cv_from_si(q, dp, rho)
        z1 = zeta_from_si(q, dp, rho, math.pi * (diameter / 1000)**2 / 4)
        self.assertAlmostEqual(z1, zeta_from_cv(cv, diameter), places=12)
        self.assertAlmostEqual(zeta_from_cv(2 * cv, diameter), z1 / 4)

    def test_invalid_inputs(self):
        for q, dp, rho in [(0, 1, 1), (-1, 1, 1), (1, 0, 1),
                           (1, -1, 1), (1, 1, 0), (1, math.nan, 1),
                           (True, 1, 1), (1, 1, math.inf)]:
            with self.subTest(q=q, dp=dp, rho=rho):
                with self.assertRaises(ValueError):
                    cv_from_si(q, dp, rho)
        with self.assertRaises(ValueError):
            zeta_from_cv(10, 0)

class AuditTests(unittest.TestCase):
    def make_project(self, root):
        (root / "1.xmlconfig").write_text(
            '<broken value="a<b"/><Surface_Goal index="0">'
            '<GUID value="pressure-guid"/><Name value="SG 密比压 平均"/>'
            '<Goal_Type value="0"/><Goal_Calc_Value value="10"/>'
            '<Faces_Keys size="2"> 616 617 </Faces_Keys></Surface_Goal>',
            encoding="utf-8")
        (root / "1.info.json").write_text(json.dumps({
            "finished": False, "settings": {"structural": False},
            "goals": [{"goal": {"name": "SG 密比压 平均", "value": 100.0,
                               "unit": "pressure and stress", "progress": 100}}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "1.geom").write_bytes(b"new-geometry")
        (root / "1.fld").write_bytes(b"old-field")
        os.utime(root / "1.fld", (1000, 1000))
        os.utime(root / "1.geom", (2000, 2000))
        (root / "Goals.DAT").mkdir()
        (root / "Goals.DAT" / "SG 密比压 平均.txt").write_text(
            "Iteration\tValue\tAvValue\tProgress\n87\t100\t90\t100\n", encoding="utf-8")

    def test_misnamed_pressure_not_contact_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_project(root)
            result = inspect_flow_project(root)
            goal = result["surface_goals"][0]
            self.assertEqual(goal["physical_quantity"], "fluid_static_pressure")
            self.assertEqual(goal["GoalID"], "pressure-guid")
            self.assertEqual(goal["Faces_Keys"], ["616", "617"])
            self.assertEqual(goal["info_minus_dat_last"], 0)
            self.assertEqual(goal["info_minus_dat_average"], 10)
            self.assertFalse(result["whole_xml_valid"])
            self.assertTrue(all(v is None for v in result["training_labels"].values()))

    def test_stale_results_and_true_manifest_cannot_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_project(root)
            (root / "run_manifest.json").write_text(
                '{"finished": true, "valid": true, "publishable": true}', encoding="utf-8")
            result = inspect_flow_project(root)
            self.assertFalse(result["publishable"])
            self.assertIn("solver_finished_not_true", result["blocking_reasons"])
            self.assertIn("result_older_than_geometry:1.fld", result["blocking_reasons"])
            self.assertIn("verified_run_geometry_result_link_missing", result["blocking_reasons"])
            self.assertIsNone(result["training_labels"]["Cv"])

    def test_no_silent_project_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1.xmlconfig").write_text("<a/>")
            (root / "2.xmlconfig").write_text("<a/>")
            result = inspect_flow_project(root)
            self.assertIn("xmlconfig_missing_or_ambiguous", result["blocking_reasons"])

if __name__ == "__main__":
    unittest.main()
