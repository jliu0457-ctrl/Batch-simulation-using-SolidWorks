"""Synthetic software fixtures only; no CAD/solver execution or training data."""
import copy
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from valve_mapping import cad_bridge as bridge
from valve_mapping import runs
from valve_mapping.__main__ import main

DESIGN = {"c_mm": 32.0, "e_mm": 3.7, "phi_deg": 8.25, "alpha_deg": 35.5,
          "Dmax_mm": 191.3178, "bm_mm": 7.5, "ds_mm": 45.0}
PROTOCOL = {"protocol_id": "synthetic_geometry_only", "fixture": True,
            "geometry": bridge.geometry_protocol()}


class CadBridgeTests(unittest.TestCase):
    def setUp(self):
        parent = runs.TASK_ROOT / "working"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="unittest_cad_bridge_", dir=parent)
        self.base = Path(self.temp.name)
        self.source = self.base / "synthetic_source"
        self.source.mkdir()
        self.report = self.source / "mapping_result.json"
        core = [f"synthetic_part_{i}.SLDPRT" for i in range(6)]
        caps = [f"封盖{i}.SLDPRT" for i in range(1, 9)]
        self.names = ["synthetic_assembly.SLDASM"] + core + caps
        for name in self.names:
            (self.source / name).write_bytes(("SYNTHETIC UNIT TEST; NOT CAD: " + name).encode())
        self.doc = self.fixture()
        self.write_report()

    def tearDown(self):
        # TemporaryDirectory contains only fixtures created by this test.
        self.temp.cleanup()

    def fixture(self):
        direct = [("c_mm", "c_mm"), ("e_mm", "e_mm"), ("phi_deg", "phi_deg"),
                  ("alpha_deg/2", "alpha_deg"), ("bm_mm", "bm_mm"), ("ds_mm", "ds_mm"),
                  ("alpha_deg", "alpha_deg")]
        writes, saved = [], []
        for index, (variable, field) in enumerate(direct):
            value = (math.radians(DESIGN[field]) / (2 if variable == "alpha_deg/2" else 1)
                     if field.endswith("_deg") else DESIGN[field] / 1000)
            parameter = f"D{index + 1}@synthetic_sketch"
            file = self.names[1 + index % 6]
            writes.append({"file": file, "parameter": parameter, "variable": variable,
                           "before_SI": value, "requested_SI": value,
                           "after_SI": value, "set_status": 0})
            saved.append({"file": file, "parameter": parameter, "value_SI": value, "expected_SI": value})
        # Repeated writes mimic inverse solve; only the final write must persist.
        for value in (0.194, 0.195):
            writes.append({"file": self.names[1], "parameter": "D99@synthetic_sketch",
                           "variable": "internal_s_mm", "before_SI": 0.194,
                           "requested_SI": value, "after_SI": value, "set_status": 0})
        saved.append({"file": self.names[1], "parameter": "D99@synthetic_sketch",
                      "value_SI": 0.195, "expected_SI": 0.195})
        lower = DESIGN["Dmax_mm"] + 0.0001
        upper = lower / math.cos(math.pi / 2048) + 1e-12 * lower
        caliper = {"lower_mm": lower, "upper_mm": upper, "midpoint_mm": (lower + upper) / 2,
                   "band_mm": upper - lower, "directions": 1024, "support_calls": 2048,
                   "step_deg": 180 / 1024, "plane": "synthetic projected closed plane",
                   "bound": "Adaptive periodic angular intervals: synthetic circle support fixture",
                   "samples": [{"angle_deg": i * 180 / 1024, "width_mm": lower} for i in range(1024)]}
        six = {k: DESIGN[k] for k in bridge.SIX_FIELDS}
        axis = {"axis_offset_mm": 0.0, "axis_abs_dot": 1.0}
        refs = [{"name": name, "path": str(self.source / name), "is_virtual": False}
                for name in self.names[1:]]
        refs.append({"name": "synthetic_embedded", "path": "temporary_virtual.SLDPRT", "is_virtual": True})
        result = {
            "fixture": "SYNTHETIC SOFTWARE TEST, NOT AN EXECUTED CAD REPORT",
            "input": copy.deepcopy(DESIGN), "completed": True,
            "physical_mapping_verified": True, "training_ready": False,
            "fixed_endcaps_verified": True, "reopened_fixed_endcaps_verified": True,
            "folder": str(self.source), "writes": writes, "persisted_readback": saved,
            "internal_s_mm": 195.0, "caliper_after_reopen": caliper,
            "Dmax_definition": "synthetic test projected caliper diameter",
            "diameter_solve_trace": [{"internal_s_mm": 195.0, "completed": True}],
            "analytic_geometry_after_reopen": {**six, "reference": "synthetic analytic entities"},
            "angle_sweep": [
                {"requested_angle_deg": 45, "actual_angle_deg": 45, "completed": True,
                 "feature_errors": [], "geometry": copy.deepcopy(axis)}],
            "references": refs, "reopened_references": copy.deepcopy(refs),
            "reopened_axis": axis,
            "cad_hashes_after": [{"file": name, "sha256": runs.file_hash(self.source / name)}
                                 for name in self.names],
        }
        return {"id": "synthetic", "folder": str(self.source), "result": result}

    def write_report(self, document=None):
        if document is not None:
            self.doc = document
        self.report.write_text(json.dumps(self.doc, allow_nan=False), encoding="utf-8")

    def imported(self, run_id="r1"):
        return bridge.import_cad_geometry_run(self.base / "runs", "synthetic_design", self.report,
                                             PROTOCOL, run_id=run_id)

    def wrapper(self):
        result = self.doc["result"]
        hashes = {r["file"]: r["sha256"] for r in result["cad_hashes_after"]}
        return {
            "wrapper_version": "single_design_v1", "input": copy.deepcopy(DESIGN),
            "status": "geometry_verified", "completed": True, "physical_mapping_verified": True,
            "training_ready": False, "simulation_labels_generated": False,
            "template_unchanged": True, "cad_executed": True, "temporary_environment_restored": True,
            "template_hashes_before": hashes, "template_hashes_after": hashes,
            "clone_hashes_before": hashes, "clone_hashes_after": hashes,
            "execution": {"result": copy.deepcopy(result), "command_in_progress_restored": True},
        }

    def test_import_retains_intervals_readback_and_fifteen_hashes_without_labels(self):
        source_hashes = {p.name: runs.file_hash(p) for p in self.source.iterdir()}
        run = self.imported()
        manifest = runs.resume_run(run, DESIGN, PROTOCOL)
        self.assertEqual(manifest["status"], "geometry_verified")
        self.assertIs(manifest["training_ready"], False)
        self.assertNotIn("Dmax_mm", manifest["cad_readback"])
        interval = manifest["geometry_intervals"]["Dmax_mm"]
        self.assertGreater(interval["lower_mm"], DESIGN["Dmax_mm"])
        self.assertNotEqual(interval["midpoint_mm"], DESIGN["Dmax_mm"])
        self.assertEqual(manifest["geometry_measured"]["alpha_deg"], DESIGN["alpha_deg"])
        mapping = runs._read_json(run / manifest["mapping"]["path"])
        self.assertAlmostEqual(mapping["cad_readback"]["alpha_deg"], DESIGN["alpha_deg"])
        self.assertEqual(mapping["construction"]["internal_s_mm"], 195.0)
        self.assertTrue(any(r.get("conversion") == "degrees(value_SI)*2" for r in mapping["control_readbacks"]))
        bundle = runs._read_json(run / "cad_bundle.json")
        self.assertEqual(len(bundle["files"]), 15)
        self.assertEqual({p.name: runs.file_hash(p) for p in self.source.iterdir()}, source_hashes)
        self.assertEqual(runs.file_hash(run / "evidence" / "mapping_result.json"), runs.file_hash(self.report))
        published = runs.publish_training_row(run, {}, {})
        self.assertFalse(published["publishable"])
        self.assertIsNone(published["row"])
        self.assertTrue(all(v is None for v in published["labels"].values()))

    def test_all_supported_report_shapes_and_wrapper_input(self):
        result = copy.deepcopy(self.doc["result"])
        for index, document in enumerate((copy.deepcopy(self.doc), result, self.wrapper())):
            with self.subTest(shape=index):
                self.write_report(document)
                self.assertEqual(bridge.inspect_cad_mapping(self.report)["design"], DESIGN)

    def test_wrapper_failed_restoration_top_status_and_hash_disagreement_rejected(self):
        wrapper = self.wrapper()
        cases = [
            ("status", "execution_failed"), ("completed", False), ("template_unchanged", False),
            ("temporary_environment_restored", False), ("training_ready", True),
            ("clone_hashes_after", {}), ("template_hashes_after", {}),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                bad = copy.deepcopy(wrapper)
                bad[key] = value
                self.write_report(bad)
                with self.assertRaises(ValueError):
                    bridge.inspect_cad_mapping(self.report)
        for update in ({"command_in_progress_restored": False}, {"error": "failed"}):
            bad = copy.deepcopy(wrapper)
            bad["execution"].update(update)
            self.write_report(bad)
            with self.assertRaises(ValueError):
                bridge.inspect_cad_mapping(self.report)

    def test_nonempty_error_even_with_success_flags_rejected(self):
        for level in ("outer", "inner"):
            bad = copy.deepcopy(self.doc)
            (bad if level == "outer" else bad["result"])["error"] = "hash collection failed"
            self.write_report(bad)
            with self.assertRaises(ValueError):
                bridge.inspect_cad_mapping(self.report)
            bad.pop("error", None)
            bad["result"].pop("error", None)
            self.doc = bad

    def test_bad_source_part_hash_missing_or_extra_part_rejected_before_run_creation(self):
        part = self.source / self.names[2]
        original = part.read_bytes()
        part.write_bytes(b"modified synthetic part")
        with self.assertRaises(ValueError):
            self.imported()
        self.assertFalse((self.base / "runs").exists())
        part.write_bytes(original)
        extra = self.source / "synthetic_extra.SLDPRT"
        extra.write_bytes(b"extra synthetic")
        with self.assertRaises(ValueError):
            bridge.inspect_cad_mapping(self.report)
        extra.unlink()
        part.unlink()
        with self.assertRaises(ValueError):
            bridge.inspect_cad_mapping(self.report)

    def test_part_only_change_rejected_on_resume_and_publication(self):
        run = self.imported()
        before = runs.file_hash(run / "cad_bundle.json")
        (run / "cad" / self.names[7]).write_bytes(b"part modified without touching assembly or bundle")
        self.assertEqual(before, runs.file_hash(run / "cad_bundle.json"))
        with self.assertRaisesRegex(ValueError, "CAD bundle file hash changed"):
            runs.resume_run(run, DESIGN, PROTOCOL)
        published = runs.publish_training_row(run, {}, {})
        self.assertFalse(published["publishable"])
        self.assertIn("CAD bundle file hash changed", published["issues"][0])

    def test_raw_report_and_normalized_evidence_tampering_rejected(self):
        run = self.imported()
        raw = run / "evidence" / "mapping_result.json"
        raw.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "raw CAD report hash changed"):
            runs.resume_run(run, DESIGN, PROTOCOL)
        run2 = self.imported("r2")
        manifest = runs._manifest(run2)
        path = run2 / manifest["mapping"]["path"]
        mapping = runs._read_json(path)
        mapping["geometry_measured"]["c_mm"] = DESIGN["c_mm"] + 0.0001
        path.write_text(json.dumps(mapping), encoding="utf-8")
        manifest["mapping"]["sha256"] = runs.file_hash(path)
        runs._save_manifest(run2, manifest)
        with self.assertRaisesRegex(ValueError, "normalized CAD mapping differs"):
            runs.resume_run(run2, DESIGN, PROTOCOL)

    def test_physical_tolerance_and_full_Dmax_interval_required(self):
        baseline = copy.deepcopy(self.doc)
        for field, delta in (("c_mm", 0.0011), ("phi_deg", 1.1e-5)):
            self.write_report(copy.deepcopy(baseline))
            self.doc["result"]["analytic_geometry_after_reopen"][field] += delta
            self.write_report()
            with self.assertRaises(ValueError):
                bridge.inspect_cad_mapping(self.report)
        self.write_report(copy.deepcopy(baseline))
        # Midpoint is within tolerance, but the whole interval is not.
        interval = self.doc["result"]["caliper_after_reopen"]
        self.doc["result"]["input"]["Dmax_mm"] = interval["lower_mm"] - 0.0009
        self.write_report()
        with self.assertRaisesRegex(ValueError, "entire measured Dmax interval"):
            bridge.inspect_cad_mapping(self.report)

    def test_Dmax_interval_arithmetic_and_sample_bounds_cannot_be_forged(self):
        baseline = copy.deepcopy(self.doc)
        cases = [
            {"band_mm": 0}, {"midpoint_mm": DESIGN["Dmax_mm"]},
            {"upper_mm": DESIGN["Dmax_mm"] - 1},
            {"directions": 16}, {"step_deg": 180},
        ]
        for overrides in cases:
            self.doc = copy.deepcopy(baseline)
            self.doc["result"]["caliper_after_reopen"].update(overrides)
            self.write_report()
            with self.assertRaises(ValueError):
                bridge.inspect_cad_mapping(self.report)

    def test_control_coverage_half_angle_fixed_opening_and_global_geometry_required(self):
        baseline = copy.deepcopy(self.doc)
        mutations = [
            lambda r: r["persisted_readback"].pop(),
            lambda r: r["persisted_readback"][3].update(value_SI=math.radians(DESIGN["alpha_deg"])),
            lambda r: r["angle_sweep"][0].update(actual_angle_deg=44),
            lambda r: r["angle_sweep"][0].update(requested_angle_deg=0),
            lambda r: r["reopened_axis"].update(axis_abs_dot=0.9),
            lambda r: r["reopened_references"][0].update(path=str(self.base / self.names[1])),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                self.doc = copy.deepcopy(baseline)
                mutate(self.doc["result"])
                self.write_report()
                with self.assertRaises(ValueError):
                    bridge.inspect_cad_mapping(self.report)

    def test_protocol_is_explicit_and_cannot_be_widened(self):
        for protocol in ({"protocol_id": "missing"},
                         {**PROTOCOL, "geometry": {**bridge.geometry_protocol(), "length_tolerance_mm": 1}}):
            with self.assertRaises(ValueError):
                bridge.import_cad_geometry_run(self.base / "runs", "d", self.report, protocol)
        self.assertFalse((self.base / "runs").exists())
        returned = bridge.geometry_protocol()
        returned["angle_tolerance_deg"] = 10
        self.assertEqual(bridge.geometry_protocol()["angle_tolerance_deg"], 1e-5)

    def test_existing_run_never_overwritten_and_created_run_supported(self):
        run = runs.create_run(self.base / "runs", "manual", DESIGN, PROTOCOL, run_id="r1")
        bridge.record_cad_geometry(run, self.report)
        with self.assertRaises(ValueError):
            bridge.record_cad_geometry(run, self.report)
        imported = self.imported()
        digest = runs.file_hash(imported / "manifest.json")
        with self.assertRaises(FileExistsError):
            self.imported()
        self.assertEqual(runs.file_hash(imported / "manifest.json"), digest)
        wrong = runs.create_run(self.base / "runs", "wrong", {**DESIGN, "c_mm": 33},
                                PROTOCOL, run_id="r1")
        with self.assertRaises(ValueError):
            bridge.record_cad_geometry(wrong, self.report)

    def test_import_cad_cli(self):
        protocol = self.base / "synthetic_protocol.json"
        protocol.write_text(json.dumps(PROTOCOL), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["import-cad", str(self.report), "--design-id", "cli_synthetic",
                           "--run-id", "r1", "--base", str(self.base / "runs"),
                           "--protocol", str(protocol)])
        self.assertEqual(status, 0, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "geometry_verified")
        self.assertFalse(result["solver_started"])
        self.assertFalse(result["training_labels_ready"])


if __name__ == "__main__":
    unittest.main()
