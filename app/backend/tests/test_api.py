"""
API smoke tests (in-process, via FastAPI TestClient — no server, no curl).

Exercises the HTTP layer end to end: importing a part through the API makes it
schematic-ready and the audit reports the library healthy. Requires fastapi +
httpx (requirements-dev.txt); skipped automatically if unavailable.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


SYMBOL = (
    '(kicad_symbol_lib (version 20211014) (generator "easyeda2kicad")\n'
    '  (symbol "ACME9" (property "Footprint" "ACME9:SOT23-6" (id 2) (at 0 0 0)))\n'
    ')\n'
)
FOOTPRINT = '(footprint "SOT23-6"\n  (layer "F.Cu")\n)\n'


@unittest.skipUnless(_HAS_FASTAPI, "fastapi/httpx not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        # Point the library at a throwaway dir so the real libs/ are untouched.
        os.environ["HWKIT_LIBS"] = str(root / "libs")

        self.zip_path = root / "part.zip"
        with zipfile.ZipFile(self.zip_path, "w") as zf:
            zf.writestr("ACME.kicad_sym", SYMBOL)
            zf.writestr("SOT23-6.kicad_mod", FOOTPRINT)
            zf.writestr("SOT23-6.step", "FAKE STEP")

        from hwkit.api.app import app
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("HWKIT_LIBS", None)
        self._tmp.cleanup()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_import_then_audit_is_healthy(self):
        with self.zip_path.open("rb") as fh:
            r = self.client.post(
                "/api/library/import",
                files={"file": ("part.zip", fh, "application/zip")},
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["symbols"], ["ACME9"])

        audit = self.client.get("/api/library/audit").json()
        self.assertEqual(audit["symbols"], 1)
        self.assertTrue(audit["healthy"], audit)

        catalog = self.client.get("/api/library/catalog").json()
        self.assertEqual(len(catalog), 1)
        self.assertTrue(catalog[0]["footprint_ok"])
        self.assertEqual(catalog[0]["footprint"], "MyFootprints:SOT23-6")

    def test_pins_packages_endpoint(self):
        # 200 with the DB present, 503 if it isn't — both are valid.
        r = self.client.get("/api/pins/packages")
        self.assertIn(r.status_code, (200, 503))
        if r.status_code == 200:
            self.assertIsInstance(r.json(), list)


if __name__ == "__main__":
    unittest.main()
