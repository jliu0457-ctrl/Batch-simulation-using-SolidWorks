"""Black-box CLI behavior and rejection of unsafe output destinations."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def call(*args):
    return subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "valve_mapping", *args],
        cwd=ROOT, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False,
    )


class CommandLineTests(unittest.TestCase):
    def test_cv_and_zeta_unit_calculation(self):
        out = call("cv", "--q-m3-s", "0.1", "--delta-p-pa", "10000",
                   "--rho-kg-m3", "1000", "--diameter-ref-mm", "200")
        self.assertEqual(out.returncode, 0, out.stderr)
        value = json.loads(out.stdout)
        self.assertAlmostEqual(value["zeta"], 1.9739208802178716, places=12)
        self.assertAlmostEqual(value["zeta"], value["zeta_direct_check"], places=12)
        self.assertGreater(value["Cv"], 1300)
        self.assertLess(value["Cv"], 1320)
        self.assertFalse(value["publishable"])

    def test_engineering_cached_value_through_cli(self):
        payload = {
            "operation": "sealing_pressure",
            "params": {"P_MPa": 3.9, "b_M_mm": 7.5, "D_MN_mm": 94,
                       "q_allow_MPa": 40, "material_branch": "seat_workbook_1p8_0p9"},
        }
        out = call("engineering", json.dumps(payload))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertAlmostEqual(json.loads(out.stdout)["q_calc_MPa"],
                               19.326459858793825, places=12)

    def test_validate_design_does_not_claim_geometry_is_verified(self):
        payload = {"c_mm": 20, "e_mm": 8, "phi_deg": 8, "alpha_deg": 20,
                   "Dmax_mm": 191.32, "bm_mm": 1.5, "ds_mm": 45}
        out = call("validate-design", json.dumps(payload))
        self.assertEqual(out.returncode, 0, out.stderr)
        value = json.loads(out.stdout)
        self.assertTrue(value["valid_input_schema"])
        self.assertFalse(value["geometry_validated"])
        self.assertFalse(value["cad_mapping_verified"])
        self.assertFalse(value["optimization_bounds_checked"])

    def test_missing_unit_field_and_duplicate_json_fail(self):
        out = call("validate-design", '{"c":20}')
        self.assertEqual(out.returncode, 2)
        self.assertIn("error", json.loads(out.stderr))
        out = call("engineering", '{"operation":"a","operation":"b"}')
        self.assertEqual(out.returncode, 2)
        self.assertIn("duplicate JSON", json.loads(out.stderr)["error"])

    def test_nonfinite_input_and_nonpositive_pressure_fail(self):
        out = call("validate-design", '{"c_mm":NaN}')
        self.assertEqual(out.returncode, 2)
        self.assertIn("nonfinite", json.loads(out.stderr)["error"])
        out = call("cv", "--q-m3-s", "0.1", "--delta-p-pa", "0", "--rho-kg-m3", "1000")
        self.assertEqual(out.returncode, 2)
        self.assertIn("error", json.loads(out.stderr))

    def test_output_traversal_rejected_before_any_write(self):
        target = ROOT.parent / "cli_never_write_outside_scope_20260914.json"
        self.assertFalse(target.exists(), "test destination must be absent")
        out = call("cv", "--q-m3-s", "0.1", "--delta-p-pa", "10000",
                   "--rho-kg-m3", "1000", "--out", "../" + target.name)
        self.assertEqual(out.returncode, 2)
        self.assertIn("inside", json.loads(out.stderr)["error"])
        self.assertFalse(target.exists())

    def test_existing_contract_file_is_not_overwritten(self):
        contract = ROOT / "config" / "response_contract.json"
        before = hashlib.sha256(contract.read_bytes()).hexdigest()
        out = call("cv", "--q-m3-s", "0.1", "--delta-p-pa", "10000",
                   "--rho-kg-m3", "1000", "--out", str(contract))
        self.assertEqual(out.returncode, 2)
        self.assertIn("overwrite", json.loads(out.stderr)["error"])
        self.assertEqual(hashlib.sha256(contract.read_bytes()).hexdigest(), before)

    def test_create_run_rejects_outside_root_before_create(self):
        target = ROOT.parent / "cli_forbidden_runs_20260914"
        self.assertFalse(target.exists())
        payload = {"design_id": "D001", "design": {}, "protocol": {"protocol_id": "test"}}
        out = call("create-run", json.dumps(payload), "--base", str(target))
        self.assertEqual(out.returncode, 2)
        self.assertIn("inside", json.loads(out.stderr)["error"])
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
