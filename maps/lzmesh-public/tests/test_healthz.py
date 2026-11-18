import unittest
from unittest.mock import patch
import app

class Liveness(unittest.TestCase):
    def test_liveness_never_calls_dependency_or_cache(self):
        with app.app.test_client() as client, patch.object(app, "fetch_atlas_nodes", side_effect=AssertionError("dependency called")), patch.object(app._nodes_cache, "status", side_effect=AssertionError("cache called")):
            response=client.get("/healthz")
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json,{"status":"ok","check":"liveness","dependencies_checked":False})
            self.assertEqual(response.headers["Cache-Control"],"no-store")
