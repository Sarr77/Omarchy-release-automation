"""Catalog and project isolation checks for the standalone publisher."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release_policy as policy
from test_release_publication import catalog, SHA


class PolicyTests(unittest.TestCase):
    def test_exact_installable_snapshot_is_accepted(self):
        self.assertEqual(policy.approved_commit(catalog()), SHA)

    def test_upstream_display_does_not_override_exact_snapshot_evidence(self):
        value = catalog()
        value["plugins"][0].update(verificationStatus="unverified", verificationCoverage="update-unverified",
                                   upstreamObservedCommit="a" * 40)
        self.assertEqual(policy.approved_commit(value), SHA)

    def test_wrong_plugin_layout_manifest_or_install_state_is_rejected(self):
        for change in ({"id": "another.plugin"}, {"repositoryLayout": "multi-plugin"},
                       {"manifestPath": "other/manifest.json"}, {"installAvailable": False},
                       {"placeholder": True}, {"builtIn": True}, {"sourceType": "built-in"}):
            value = catalog()
            value["plugins"][0].update(change)
            with self.assertRaises(policy.UnverifiedUpdate): policy.approved_commit(value)

    def test_missing_or_mismatched_verification_commit_is_rejected(self):
        for key, value in (("verificationCommit", "a" * 40), ("listingValidatedCommit", "main"),
                           ("verificationSnapshotStatus", "unverified")):
            doc = catalog()
            doc["plugins"][0][key] = value
            with self.assertRaises(policy.UnverifiedUpdate): policy.approved_commit(doc)

    def test_schema_change_and_duplicate_listing_are_rejected(self):
        doc = catalog()
        doc["stateSchemaVersion"] = True
        with self.assertRaises(ValueError): policy.approved_commit(doc)
        doc = catalog()
        doc["plugins"] *= 2
        with self.assertRaises(policy.UnverifiedUpdate): policy.approved_commit(doc)

    def test_stable_version_ordering_and_invalid_values(self):
        self.assertLess(policy.version("0.11.9"), policy.version("0.11.10"))
        for value in ("v0.11.2", "0.11.2-beta", "00.11.2", None):
            with self.assertRaises(ValueError): policy.version(value)

    def test_catalog_transport_rejects_redirects_and_oversized_documents(self):
        for redirected, raw in ((True, b"{}"), (False, b"x" * 11)):
            response = io.BytesIO(raw)
            response.status = 200
            response.geturl = lambda: "https://other.example" if redirected else policy.CATALOG_URL
            with patch.object(policy.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(ValueError): policy.read_json(policy.CATALOG_URL, 10)
                self.assertIsInstance(opener.call_args.args[0], policy.NoRedirect)
                request = opener.return_value.open.call_args.args[0]
                self.assertIsNone(request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
