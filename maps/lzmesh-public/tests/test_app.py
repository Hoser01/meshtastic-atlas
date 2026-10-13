import unittest
from unittest.mock import patch

import app as map_app


class PublicMapTests(unittest.TestCase):
    def setUp(self):
        self.client = map_app.app.test_client()

    @patch.object(map_app, "fetch_atlas_nodes")
    def test_summary_counts_age_windows(self, fetch_nodes):
        fetch_nodes.return_value = {
            "live": {"ageMin": 10},
            "day": {"ageMin": 300},
            "ninety": {"ageMin": 10_000},
            "old": {"ageMin": 200_000},
        }

        response = self.client.get("/new/summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"live": 1, "day": 2, "ninety": 3, "all": 4},
        )

    @patch.object(map_app, "fetch_atlas_nodes")
    def test_node_feed_filters_and_limits_fields(self, fetch_nodes):
        fetch_nodes.return_value = {
            "!1": {
                "id": "!1",
                "name": "Example",
                "lat": 37.1,
                "lng": -93.2,
                "ageMin": 20,
                "private": "must not leak",
            },
            "!2": {"id": "!2", "ageMin": 31},
        }

        response = self.client.get("/new/nodes?period=live")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(list(payload), ["!1"])
        self.assertNotIn("private", payload["!1"])

    def test_invalid_period_is_rejected(self):
        response = self.client.get("/new/nodes?period=forever")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
