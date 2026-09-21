import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from valve_mapping.runs import (TASK_ROOT, INPUT_FIELDS, LABEL_UNITS, LABEL_SOURCES,
    validate_design, create_run, resume_run, record_geometry, publish_training_row,
    file_hash)

DESIGN = dict(zip(INPUT_FIELDS, [5, 8, 7, 20, 220, 1.5, 30]))
PROTOCOL = {"protocol_id": "test-only", "opening_deg": 45, "fixture": True}

class RunsTests(unittest.TestCase):
    def setUp(self):
        # Explicit synthetic fixtures, isolated inside the authorized task root.
        parent = TASK_ROOT / "working"
        parent.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="unittest_runs_", dir=parent)
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, data):
        path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
        return {"path": path.name, "sha256": file_hash(path)}

    def ready(self):
        run = create_run(self.base, "synthetic_design", DESIGN, PROTOCOL, run_id="test_run")
        manifest = resume_run(run, DESIGN, PROTOCOL)
        geometry = run / "synthetic.geometry"
        geometry.write_bytes(b"synthetic geometry fixture - never CAD")
        mapping = {k: manifest[k] for k in ("run_id", "design_hash", "protocol_hash")}
        mapping.update(status="verified", parameter_ids={k: "test::" + k for k in INPUT_FIELDS},
                       rebuild_ok=True, cad_readback=DESIGN, geometry_measured=DESIGN,
                       geometry_status="verified", geometry_sha256=file_hash(geometry))
        self.write(run / "mapping.json", mapping)
        manifest = record_geometry(run, DESIGN, geometry, "mapping.json")
        common = {k: manifest[k] for k in ("run_id", "design_hash", "protocol_hash")}
        common.update(geometry_sha256=file_hash(geometry), status="verified")
        raw = run / "synthetic.results"
        raw.write_text("synthetic solver result fixture, not an executed run", encoding="utf-8")
        labels = {}
        source_runs = {}
        for name, unit in LABEL_UNITS.items():
            source_runs[name] = "synthetic_" + name
            doc = {**common, "label": name, "value": 1.0, "unit": unit,
                   "source_type": LABEL_SOURCES[name], "source_run_id": source_runs[name],
                   "raw_results": [{"path": raw.name, "sha256": file_hash(raw)}]}
            labels[name] = self.write(run / (name + ".json"), doc)
        required = ("geometry_valid", "interference", "face_selection",
                    "flow_finished", "flow_convergence", "flow_mesh",
                    "contact_finished", "contact_convergence", "contact_mesh",
                    "engineering_inputs", "liquid_cv_conditions")
        quality = self.write(run / "quality.json",
                             {**common, "checks": {k: "passed" for k in required},
                              "source_run_ids": source_runs})
        return run, labels, quality

    def test_exact_finite_fields(self):
        self.assertEqual(set(validate_design(DESIGN)), set(INPUT_FIELDS))
        for bad in [{**DESIGN, "extra": 1}, {k:v for k,v in DESIGN.items() if k != "ds_mm"},
                    {**DESIGN, "c_mm": math.nan}, {**DESIGN, "alpha_deg": 0},
                    {**DESIGN, "ds_mm": True}, {**DESIGN, "bm_mm": -1}]:
            with self.assertRaises(ValueError):
                validate_design(bad)

    def test_isolation_no_overwrite_or_escape(self):
        run = create_run(self.base, "d1", DESIGN, PROTOCOL, run_id="r1")
        self.assertEqual(run.parent.name, "d1")
        with self.assertRaises(FileExistsError):
            create_run(self.base, "d1", DESIGN, PROTOCOL, run_id="r1")
        for name in ("../escape", "a/b", "CON", "a.b"):
            with self.assertRaises(ValueError):
                create_run(self.base, name, DESIGN, PROTOCOL)

    def test_resume_requires_identical_design_and_protocol(self):
        run = create_run(self.base, "d1", DESIGN, PROTOCOL, run_id="r1")
        resume_run(run, DESIGN, PROTOCOL)
        with self.assertRaises(ValueError):
            resume_run(run, {**DESIGN, "c_mm": 6}, PROTOCOL)
        with self.assertRaises(ValueError):
            resume_run(run, DESIGN, {**PROTOCOL, "opening_deg": 90})

    def test_complete_synthetic_evidence_returns_row(self):
        run, labels, quality = self.ready()
        result = publish_training_row(run, labels, quality)
        self.assertTrue(result["publishable"], result["issues"])
        self.assertEqual(result["row"]["Cv"], 1.0)
        self.assertEqual(result["row"]["c_mm"], 5.0)

    def test_blank_manifest_never_releases_labels(self):
        run = create_run(self.base, "d1", DESIGN, PROTOCOL, run_id="r1")
        result = publish_training_row(run, {}, {})
        self.assertFalse(result["publishable"])
        self.assertTrue(all(x is None for x in result["labels"].values()))

    def test_modified_geometry_and_missing_task_rejected(self):
        run, labels, quality = self.ready()
        partial = {k:v for k,v in labels.items() if k != "Cv"}
        self.assertFalse(publish_training_row(run, partial, quality)["publishable"])
        (run / "synthetic.geometry").write_bytes(b"changed geometry")
        self.assertFalse(publish_training_row(run, labels, quality)["publishable"])
        with self.assertRaises(ValueError):
            resume_run(run, DESIGN, PROTOCOL)

    def test_unknown_run_wrong_physics_and_nan_rejected(self):
        run, labels, quality = self.ready()
        target = run / labels["sigma_n_MPa"]["path"]
        original = json.loads(target.read_text())
        for overrides in ({"run_id": "other_run"},
                          {"source_type": "fluid_static_pressure"},
                          {"unit": "Pa"}, {"value": -1}, {"raw_results": []}):
            labels["sigma_n_MPa"] = self.write(target, {**original, **overrides})
            self.assertFalse(publish_training_row(run, labels, quality)["publishable"])
        target.write_text(json.dumps({**original, "value": math.nan}), encoding="utf-8")
        labels["sigma_n_MPa"]["sha256"] = file_hash(target)
        self.assertFalse(publish_training_row(run, labels, quality)["publishable"])

    def test_stale_raw_results_and_failed_quality_rejected(self):
        run, labels, quality = self.ready()
        qp = run / quality["path"]
        doc = json.loads(qp.read_text())
        doc["checks"]["flow_finished"] = "failed"
        quality = self.write(qp, doc)
        self.assertFalse(publish_training_row(run, labels, quality)["publishable"])
        doc["checks"]["flow_finished"] = "passed"
        quality = self.write(qp, doc)
        os.utime(run / "synthetic.results", (1000, 1000))
        self.assertFalse(publish_training_row(run, labels, quality)["publishable"])

    def test_wrong_geometry_readback_rejected(self):
        run = create_run(self.base, "d1", DESIGN, PROTOCOL, run_id="r1")
        manifest = resume_run(run, DESIGN, PROTOCOL)
        gp = run / "synthetic.geometry"
        gp.write_bytes(b"synthetic")
        mapping = {k: manifest[k] for k in ("run_id", "design_hash", "protocol_hash")}
        mapping.update(status="verified", parameter_ids={k:"test::"+k for k in INPUT_FIELDS},
                       rebuild_ok=True, cad_readback=DESIGN,
                       geometry_measured={**DESIGN, "phi_deg": 8},
                       geometry_status="verified", geometry_sha256=file_hash(gp))
        self.write(run / "mapping.json", mapping)
        with self.assertRaises(ValueError):
            record_geometry(run, DESIGN, gp, "mapping.json")

if __name__ == "__main__":
    unittest.main()
