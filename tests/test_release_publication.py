"""Exercise publication, withdrawal, tampering and retries without network writes."""
import base64
import copy
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("publisher", ROOT / "tools/publish_verified_release.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

SHA = "f" * 40
PLAN = {"schemaVersion": 1, "approvedForPublication": True, "repository": publisher.REPOSITORY,
        "releaseId": 123, "commit": SHA, "version": "0.11.2", "title": "ScratchPeek 0.11.2",
        "body": "Reviewed release notes.\n", "assets": [
            {"name": "ScratchPeek-0.11.2.zip", "size": 321, "digest": "sha256:" + "a" * 64, "label": ""}]}
PUBLICATION = {"draft": False, "make_latest": "true", "tag_name": "v0.11.2", "target_commitish": SHA}


class FakeGitHub:
    def __init__(self):
        self.plan = copy.deepcopy(PLAN)
        self.release = {"id": 123, "draft": True, "prerelease": False, "immutable": False,
                        "name": PLAN["title"], "body": PLAN["body"], "tag_name": "v0.11.2",
                        "target_commitish": SHA, "author": {"id": publisher.AUTHOR_ID},
                        "html_url": "https://github.com/Sarr77/ScratchPeek/releases/tag/v0.11.2"}
        self.assets = [dict(a, state="uploaded") for a in PLAN["assets"]]
        self.tag = {"ref": "refs/tags/v0.11.2", "object": {"type": "commit", "sha": SHA}}
        self.manifest = {"id": "sarr.scratchpeek", "version": "0.11.2", "author": "Sarr"}
        self.immutable = True
        self.latest = {"id": 122, "tag_name": "v0.11.1", "draft": False, "prerelease": False}
        self.calls = []
        self.writes = []
        self.before_get = lambda path, count: None
        self.after_patch = lambda: None
        self.deleted = False

    def get(self, path):
        self.calls.append(path)
        self.before_get(path, self.calls.count(path))
        if path == "":
            value = {"full_name": publisher.REPOSITORY, "private": False, "visibility": "public", "default_branch": "main"}
        elif path == "/actions/variables/" + publisher.PLAN_VARIABLE:
            if self.deleted:
                raise publisher.Blocked("authorization withdrawn")
            value = {"name": publisher.PLAN_VARIABLE, "value": json.dumps(self.plan)}
        elif path == "/releases/123":
            value = self.release
        elif path == "/releases/123/assets?per_page=100":
            value = self.assets
        elif path == "/git/ref/tags/v0.11.2":
            value = self.tag
        elif path == "/git/tags/" + "b" * 40:
            value = {"object": {"type": "commit", "sha": SHA}}
        elif path == "/contents/manifest.json?ref=" + SHA:
            value = {"encoding": "base64", "type": "file",
                     "content": base64.b64encode(json.dumps(self.manifest).encode()).decode()}
        elif path == "/immutable-releases":
            value = {"enabled": self.immutable}
        elif path == "/releases/latest":
            value = self.latest
        else:
            raise AssertionError("Unexpected API read: " + path)
        return copy.deepcopy(value)

    def request(self, path, method="GET", payload=None):
        self.writes.append((method, path, payload))
        if method == "PATCH" and path == publisher.PREFIX + "/releases/123":
            assert payload == PUBLICATION
            self.release.update(draft=False, immutable=self.immutable)
            self.latest = self.release
            self.after_patch()
            return copy.deepcopy(self.release)
        if method == "DELETE" and path == publisher.PLAN_PREFIX + "/actions/variables/" + publisher.PLAN_VARIABLE:
            assert payload is None
            self.deleted = True
            return None
        raise AssertionError("Unexpected API mutation")


class ReleaseAPI:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def get(self, path):
        assert not path.startswith("/actions/")
        self.calls.append(("GET", path))
        return self.state.get(path)

    def request(self, path, method="GET", payload=None):
        assert method == "PATCH" and path.startswith(publisher.PREFIX + "/releases/")
        self.calls.append((method, path))
        return self.state.request(path, method, payload)


class PlanAPI:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def get(self, path):
        assert path == "/actions/variables/" + publisher.PLAN_VARIABLE
        self.calls.append(("GET", path))
        return self.state.get(path)

    def request(self, path, method="GET", payload=None):
        assert method == "DELETE" and path == publisher.PLAN_PREFIX + "/actions/variables/" + publisher.PLAN_VARIABLE
        self.calls.append((method, path))
        return self.state.request(path, method, payload)


def catalog():
    return {"stateSchemaVersion": 2, "plugins": [{
        "id": "sarr.scratchpeek", "repo": "https://github.com/Sarr77/ScratchPeek",
        "sourceType": "community", "repositoryLayout": "root-plugin", "manifestPath": "manifest.json",
        "installAvailable": True, "verificationSnapshotStatus": "verified",
        "listingValidatedCommit": SHA, "verificationCommit": SHA}]}


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.catalog = catalog()
        self.releases = ReleaseAPI(self.api)
        self.plans = PlanAPI(self.api)
        self.worker = publisher.Publisher(self.releases, self.plans, lambda: copy.deepcopy(self.catalog))

    def blocked_without_writes(self):
        with self.assertRaises((publisher.Blocked, ValueError)):
            self.worker.run(PLAN, publish=True)
        self.assertEqual(self.api.writes, [])

    def test_read_only_check_and_exact_publication_then_disarm(self):
        self.assertEqual(self.worker.run(PLAN), "ready")
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.worker.run(PLAN, publish=True), "published")
        self.assertEqual(self.api.writes, [
            ("PATCH", publisher.PREFIX + "/releases/123", PUBLICATION),
            ("DELETE", publisher.PLAN_PREFIX + "/actions/variables/" + publisher.PLAN_VARIABLE, None)])
        self.assertTrue(self.api.deleted)

    def test_unverified_or_different_snapshot_waits_without_publishing(self):
        for changed in ("missing", "unverified", "different", "different-repository", "duplicate"):
            with self.subTest(changed=changed):
                self.setUp()
                entry = self.catalog["plugins"][0]
                if changed == "missing": self.catalog["plugins"] = []
                if changed == "unverified": entry["verificationSnapshotStatus"] = "pending"
                if changed == "different": entry.update(listingValidatedCommit="a" * 40, verificationCommit="a" * 40)
                if changed == "different-repository": entry["repo"] = "https://github.com/other/ScratchPeek"
                if changed == "duplicate": self.catalog["plugins"].append(copy.deepcopy(entry))
                self.assertEqual(self.worker.run(PLAN, publish=True), "waiting-for-marketplace")
                self.assertEqual(self.api.writes, [])

    def test_catalog_protocol_change_blocks(self):
        self.catalog["stateSchemaVersion"] = 3
        self.blocked_without_writes()

    def test_authorization_must_match_snapshot(self):
        self.api.plan["body"] = "A different approved text"
        self.blocked_without_writes()

    def test_withdrawn_authorization_just_before_publication(self):
        def revoke(path, count):
            if path == "/actions/variables/" + publisher.PLAN_VARIABLE and count == 2:
                self.api.plan["approvedForPublication"] = False
        self.api.before_get = revoke
        self.blocked_without_writes()

    def test_rejects_changed_draft_text_target_author_or_extra_discussion(self):
        for key, value in (("name", "New title"), ("body", "New notes"), ("target_commitish", "main"),
                           ("author", {"id": 1}), ("prerelease", True), ("discussion_url", "https://github.com/discussion")):
            with self.subTest(key=key):
                self.setUp()
                self.api.release[key] = value
                self.blocked_without_writes()

    def test_rejects_changed_missing_extra_or_incomplete_assets(self):
        for key, value in (("digest", "sha256:" + "b" * 64), ("size", 322), ("size", True),
                           ("name", "another.zip"), ("label", "changed"), ("label", False), ("state", "new")):
            with self.subTest(key=key):
                self.setUp()
                self.api.assets[0][key] = value
                self.blocked_without_writes()
        for assets in ([], self.api.assets * 2):
            self.setUp()
            self.api.assets = assets
            self.blocked_without_writes()

    def test_annotated_tag_is_resolved_without_moving_it(self):
        self.api.tag["object"] = {"type": "tag", "sha": "b" * 40}
        self.assertEqual(self.worker.run(PLAN), "ready")
        self.assertEqual(self.api.writes, [])

    def test_wrong_or_changed_tag_and_manifest_are_blocked(self):
        self.api.tag["object"]["sha"] = "a" * 40
        self.blocked_without_writes()
        self.setUp()
        self.api.manifest["version"] = "0.11.3"
        self.blocked_without_writes()
        self.setUp()
        def change(path, count):
            if path == "/git/ref/tags/v0.11.2" and count == 2:
                self.api.tag["object"]["sha"] = "a" * 40
        self.api.before_get = change
        self.blocked_without_writes()

    def test_immutable_setting_disabled_or_changed_blocks(self):
        self.api.immutable = False
        self.blocked_without_writes()
        self.setUp()
        def disable(path, count):
            if path == "/immutable-releases" and count == 2: self.api.immutable = False
        self.api.before_get = disable
        self.blocked_without_writes()

    def test_does_not_replace_a_newer_latest_release(self):
        self.api.latest["tag_name"] = "v0.11.3"
        self.blocked_without_writes()
        self.setUp()
        def change(path, count):
            if path == "/releases/latest" and count == 2: self.api.latest["tag_name"] = "v0.11.3"
        self.api.before_get = change
        self.blocked_without_writes()

    def test_catalog_withdrawal_before_patch_blocks(self):
        calls = 0
        def withdrawal():
            nonlocal calls
            calls += 1
            value = catalog()
            if calls == 2: value["plugins"][0]["verificationSnapshotStatus"] = "revoked"
            return value
        self.worker.catalog = withdrawal
        self.blocked_without_writes()

    def test_retry_does_not_republish_or_overwrite_later_text_edits(self):
        self.api.release.update(draft=False, immutable=True, name="Corrected title", body="Corrected notes")
        self.assertEqual(self.worker.run(PLAN), "already-published")
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.worker.run(PLAN, publish=True), "already-published")
        self.assertEqual([w[0] for w in self.api.writes], ["DELETE"])

    def test_failed_postpublication_verification_does_not_retry_patch_or_disarm(self):
        self.api.after_patch = lambda: self.api.release.update(immutable=False)
        with self.assertRaisesRegex(publisher.Blocked, "not immutable"):
            self.worker.run(PLAN, publish=True)
        self.assertEqual([w[0] for w in self.api.writes], ["PATCH"])
        self.assertFalse(self.api.deleted)

    def test_changed_authorization_is_not_deleted_after_publication(self):
        self.api.after_patch = lambda: self.api.plan.update(body="Next authorized text")
        with self.assertRaises(publisher.Blocked):
            self.worker.run(PLAN, publish=True)
        self.assertEqual([w[0] for w in self.api.writes], ["PATCH"])

    def test_lost_patch_response_recovers_without_second_publication(self):
        def lost_response():
            raise OSError("connection closed after publication")
        self.api.after_patch = lost_response
        with self.assertRaises(OSError):
            self.worker.run(PLAN, publish=True)
        self.assertFalse(self.api.deleted)
        self.api.release.update(name="Later approved correction", body="Later approved notes")
        self.assertEqual(self.worker.run(PLAN, publish=True), "already-published")
        self.assertEqual([w[0] for w in self.api.writes], ["PATCH", "DELETE"])

    def test_failed_disarm_can_be_retried_without_editing_release(self):
        original = self.api.request
        def fail_delete(path, method="GET", payload=None):
            if method == "DELETE": raise OSError("service unavailable")
            return original(path, method, payload)
        self.api.request = fail_delete
        with self.assertRaises(OSError): self.worker.run(PLAN, publish=True)
        self.api.request = original
        self.assertEqual(self.worker.run(PLAN, publish=True), "already-published")
        self.assertEqual([w[0] for w in self.api.writes], ["PATCH", "DELETE"])

    def test_latest_failure_after_publication_keeps_plan_for_safe_retry(self):
        self.api.after_patch = lambda: setattr(self.api, "latest", {"id": 122})
        with self.assertRaisesRegex(publisher.Blocked, "did not become latest"):
            self.worker.run(PLAN, publish=True)
        self.assertFalse(self.api.deleted)
        self.api.latest = self.api.release
        self.assertEqual(self.worker.run(PLAN, publish=True), "already-published")
        self.assertEqual([w[0] for w in self.api.writes], ["PATCH", "DELETE"])

    def test_retry_blocks_changed_assets_and_nonimmutable_release(self):
        self.api.release.update(draft=False, immutable=True)
        self.api.assets[0]["digest"] = "sha256:" + "b" * 64
        self.blocked_without_writes()
        self.setUp()
        self.api.release.update(draft=False, immutable=False)
        self.blocked_without_writes()

    def test_revocation_during_last_catalog_check_is_seen_before_patch(self):
        calls = 0
        def withdraw():
            nonlocal calls
            calls += 1
            if calls == 2: self.api.plan["approvedForPublication"] = False
            return catalog()
        self.worker.catalog = withdraw
        self.blocked_without_writes()

    def test_private_repo_or_wrong_default_branch_is_rejected(self):
        original = self.api.get
        for change in ({"private": True, "visibility": "private"}, {"default_branch": "other"},
                       {"full_name": "Sarr/ScratchPeek"}):
            def get(path):
                result = original(path)
                if path == "": result.update(change)
                return result
            self.api.get = get
            self.blocked_without_writes()

    def test_tag_depth_and_wrong_ref_are_rejected(self):
        self.api.tag["ref"] = "refs/tags/v0.11.3"
        self.blocked_without_writes()
        self.setUp()
        self.api.tag["object"] = {"type": "tag", "sha": "b" * 40}
        original = self.api.get
        def cyclic_tag(path):
            if path.startswith("/git/tags/"):
                return {"object": {"type": "tag", "sha": "b" * 40}}
            return original(path)
        self.api.get = cyclic_tag
        self.blocked_without_writes()

    def test_second_draft_check_rejects_changed_notes_or_attachments(self):
        for change in ("body", "asset"):
            self.setUp()
            def tamper(path, count):
                if path == "/releases/123" and count == 2:
                    if change == "body": self.api.release["body"] = "Unapproved notes"
                    else: self.api.assets[0]["digest"] = "sha256:" + "b" * 64
            self.api.before_get = tamper
            self.blocked_without_writes()

    def test_two_reviewed_attachments_and_null_labels_are_supported(self):
        plan = copy.deepcopy(PLAN)
        plan["assets"].append({"name": "ScratchPeek-0.11.2.sha256", "size": 89,
                               "digest": "sha256:" + "b" * 64, "label": ""})
        self.api.plan = plan
        self.api.assets = [dict(a, state="uploaded", label=None) for a in plan["assets"]]
        self.assertEqual(self.worker.run(plan), "ready")
        self.assertEqual(self.api.writes, [])

    def test_invalid_plan_is_rejected(self):
        for key, value in (("approvedForPublication", False), ("releaseId", True), ("commit", "main"),
                           ("repository", "other/repo"), ("version", "0.11.2; command"), ("assets", []),
                           ("schemaVersion", True), ("extra", "unreviewed")):
            with self.subTest(key=key):
                plan = copy.deepcopy(PLAN)
                plan[key] = value
                with self.assertRaises(ValueError): publisher.parse_plan(json.dumps(plan))
        for change in ("duplicate", "unsafe-name", "no-digest"):
            plan = copy.deepcopy(PLAN)
            if change == "duplicate": plan["assets"] *= 2
            if change == "unsafe-name": plan["assets"][0]["name"] = "../outside"
            if change == "no-digest": plan["assets"][0]["digest"] = None
            with self.assertRaises(ValueError): publisher.parse_plan(json.dumps(plan))

    def test_duplicate_json_keys_and_oversized_utf8_plan_are_rejected(self):
        raw = json.dumps(PLAN)
        duplicate = raw.replace('"approvedForPublication": true',
                                '"approvedForPublication": false, "approvedForPublication": true')
        with self.assertRaisesRegex(publisher.Blocked, "duplicate"):
            publisher.parse_plan(duplicate)
        plan = copy.deepcopy(PLAN)
        plan["body"] = "ż" * 25000
        with self.assertRaisesRegex(publisher.Blocked, "variable limit"):
            publisher.parse_plan(json.dumps(plan, ensure_ascii=False))

    def test_no_plan_does_not_even_initialize_network_client(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(publisher.sys, "argv", ["publisher"]), \
                patch.object(publisher, "GitHub", side_effect=AssertionError("no network")):
            self.assertEqual(publisher.main(), 0)

    def test_cli_is_check_only_unless_publish_is_explicit(self):
        env = {publisher.PLAN_VARIABLE: json.dumps(PLAN), "SCRATCHPEEK_RELEASE_TOKEN": "test-token",
               "RELEASE_PLAN_TOKEN": "test-plan-token"}
        for publish in (False, True):
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(publisher.sys, "argv", ["publisher"] + (["--publish"] if publish else [])), \
                    patch.object(publisher, "GitHub", return_value=self.api), \
                    patch.object(publisher, "Publisher", return_value=self.worker):
                self.assertEqual(publisher.main(), 0)
                self.assertEqual(bool(self.api.writes), publish)

    def test_catalog_outage_cannot_publish(self):
        def unavailable(): raise OSError("catalog unavailable")
        self.worker.catalog = unavailable
        with self.assertRaises(OSError): self.worker.run(PLAN, publish=True)
        self.assertEqual(self.api.writes, [])

    def test_retry_does_not_demote_a_newer_release_to_latest(self):
        self.api.release.update(draft=False, immutable=True)
        self.api.latest = {"id": 124, "tag_name": "v0.11.3", "draft": False, "prerelease": False}
        self.assertEqual(self.worker.run(PLAN, publish=True), "already-published")
        self.assertEqual([w[0] for w in self.api.writes], ["DELETE"])
        self.assertEqual(self.api.latest["id"], 124)

    def test_release_and_authorization_use_separate_clients(self):
        self.assertEqual(self.worker.run(PLAN, publish=True), "published")
        self.assertTrue(self.releases.calls)
        self.assertTrue(self.plans.calls)
        self.assertFalse(any("variables" in path for _, path in self.releases.calls))
        self.assertTrue(all("variables" in path for _, path in self.plans.calls))

    def test_cli_selects_two_repository_scoped_tokens(self):
        env = {publisher.PLAN_VARIABLE: json.dumps(PLAN),
               "SCRATCHPEEK_RELEASE_TOKEN": "release-only-secret", "RELEASE_PLAN_TOKEN": "plan-only-secret"}
        with patch.dict(os.environ, env, clear=True), patch.object(publisher.sys, "argv", ["publisher"]), \
                patch.object(publisher, "GitHub") as client, patch.object(publisher, "Publisher") as worker:
            worker.return_value.run.return_value = "ready"
            self.assertEqual(publisher.main(), 0)
            self.assertEqual([call.args for call in client.call_args_list], [
                ("release-only-secret", publisher.REPOSITORY), ("plan-only-secret", publisher.AUTOMATION_REPOSITORY)])
            self.assertEqual(worker.return_value.run.call_args.args, (PLAN, False))

    def test_windowpeek_is_not_enabled_by_this_change(self):
        self.assertEqual(set(publisher.PROJECTS), {"scratchpeek"})
        with patch.object(publisher.sys, "argv", ["publisher", "--project", "windowpeek"]), \
                patch.object(publisher, "GitHub") as client, patch.object(publisher.sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit): publisher.main()
            client.assert_not_called()



class TransportTests(unittest.TestCase):
    def setUp(self):
        self.api = publisher.GitHub("test-secret-never-log")
        self.path = publisher.PREFIX + "/releases/123"

    def response(self, raw=b"{}", status=200, url=None):
        stream = io.BytesIO(raw)
        stream.status = status
        stream.geturl = lambda: url or publisher.API + self.path
        return stream

    def test_api_headers_exact_patch_and_no_redirect_handler(self):
        with patch.object(publisher.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = self.response()
            self.api.request(self.path, "PATCH", PUBLICATION)
            self.assertIsInstance(build.call_args.args[0], publisher.policy.NoRedirect)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, publisher.API + self.path)
            self.assertEqual(request.get_header("Authorization"), "Bearer test-secret-never-log")
            self.assertEqual(request.get_header("X-github-api-version"), "2026-03-10")
            self.assertEqual(json.loads(request.data), PUBLICATION)

    def test_wrong_paths_and_unapproved_write_operations_never_send_requests(self):
        cases = [("https://other.example/", "GET", None),
                 (publisher.PREFIX + "/releases", "POST", PUBLICATION),
                 (self.path, "DELETE", None), (self.path, "PATCH", {"body": "unapproved"}),
                 (publisher.PREFIX + "/actions/variables/OTHER", "DELETE", None)]
        with patch.object(publisher.urllib.request, "build_opener") as build:
            for path, method, payload in cases:
                with self.assertRaises(publisher.Blocked): self.api.request(path, method, payload)
            build.assert_not_called()

    def test_http_error_does_not_expose_error_body_or_token(self):
        for status in (301, 401, 403, 404, 422, 429, 500):
            error = urllib.error.HTTPError(publisher.API + self.path, status, "error", {},
                                           io.BytesIO(b"test-secret-never-log remote private content"))
            with patch.object(publisher.urllib.request, "build_opener") as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(publisher.Blocked) as caught: self.api.request(self.path)
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn("test-secret", str(caught.exception))
                self.assertNotIn("private content", str(caught.exception))

    def test_redirect_wrong_status_and_oversized_responses_are_rejected(self):
        for response in (self.response(url="https://other.example/"), self.response(status=201),
                         self.response(raw=b" " * (2 * 1024 * 1024 + 1))):
            with patch.object(publisher.urllib.request, "build_opener") as build:
                build.return_value.open.return_value = response
                with self.assertRaises(publisher.Blocked): self.api.request(self.path)

    def test_delete_requires_exact_204_response(self):
        path = publisher.PLAN_PREFIX + "/actions/variables/" + publisher.PLAN_VARIABLE
        plans = publisher.GitHub("test-plan-token", publisher.AUTOMATION_REPOSITORY)
        with patch.object(publisher.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = self.response(status=204, url=publisher.API + path)
            self.assertIsNone(plans.request(path, "DELETE"))
        with patch.object(publisher.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = self.response(status=200, url=publisher.API + path)
            with self.assertRaises(publisher.Blocked): plans.request(path, "DELETE")

    def test_tokens_cannot_cross_repositories_or_operation_types(self):
        plans = publisher.GitHub("plan-only", publisher.AUTOMATION_REPOSITORY)
        variable = publisher.PLAN_PREFIX + "/actions/variables/" + publisher.PLAN_VARIABLE
        with patch.object(publisher.urllib.request, "build_opener") as build:
            for client, path, method, payload in [
                (self.api, variable, "GET", None), (self.api, variable, "DELETE", None),
                (plans, self.path, "GET", None), (plans, self.path, "PATCH", PUBLICATION),
                (self.api, publisher.PREFIX + "/actions/variables/" + publisher.PLAN_VARIABLE, "GET", None),
                (plans, publisher.PLAN_PREFIX + "/contents/README.md", "GET", None),
                (self.api, publisher.PREFIX + "/git/refs/heads/main", "PATCH", {"sha": SHA}),
                (self.api, publisher.PREFIX + "/git/refs", "POST", {"sha": SHA}),
            ]:
                with self.assertRaises(publisher.Blocked): client.request(path, method, payload)
            build.assert_not_called()

    def test_plan_get_uses_only_automation_repository_and_plan_token(self):
        plans = publisher.GitHub("plan-only", publisher.AUTOMATION_REPOSITORY)
        path = publisher.PLAN_PREFIX + "/actions/variables/" + publisher.PLAN_VARIABLE
        with patch.object(publisher.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = self.response(url=publisher.API + path)
            plans.get("/actions/variables/" + publisher.PLAN_VARIABLE)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, publisher.API + path)
            self.assertEqual(request.get_header("Authorization"), "Bearer plan-only")

    def test_unconfigured_repository_is_rejected(self):
        for repository in ("Sarr77/WindowPeek", "other/repo"):
            with self.assertRaises(publisher.Blocked): publisher.GitHub("token", repository)



if __name__ == "__main__":
    unittest.main()
