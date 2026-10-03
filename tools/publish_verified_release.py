#!/usr/bin/env python3
"""Publish an explicitly authorized draft after exact marketplace verification.

No builds, generated notes, tag changes or release creation. The automation
repository variable authorizes one complete draft; clearing it disarms the job.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

import release_policy as policy
from projects import AUTOMATION_REPOSITORY, PROJECTS, SCRATCHPEEK

REPOSITORY = SCRATCHPEEK.repository
PLAN_VARIABLE = SCRATCHPEEK.plan_variable
AUTHOR_ID = SCRATCHPEEK.author_id
API = "https://api.github.com"
PREFIX = "/repos/" + REPOSITORY
PLAN_PREFIX = "/repos/" + AUTOMATION_REPOSITORY


class Blocked(ValueError):
    """Nothing may be published with these inputs."""


def require(condition, message):
    if not condition:
        raise Blocked(message)


def positive_integer(value):
    return type(value) is int and value > 0


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def parse_plan(raw, project=SCRATCHPEEK):
    require(isinstance(raw, str) and len(raw.encode("utf-8")) <= 48 * 1024,
            "release plan exceeds the repository variable limit")
    plan = json.loads(raw, object_pairs_hook=unique_fields)
    require(isinstance(plan, dict), "release plan must be an object")
    require(set(plan) == {"schemaVersion", "approvedForPublication", "repository", "releaseId",
                          "commit", "version", "title", "body", "assets"}, "unexpected release plan fields")
    require(type(plan["schemaVersion"]) is int and plan["schemaVersion"] == 1, "unsupported release plan")
    require(plan["approvedForPublication"] is True, "release plan has not been authorized")
    require(plan["repository"] == project.repository, "wrong release repository")
    require(positive_integer(plan["releaseId"]), "missing draft release ID")
    require(isinstance(plan["commit"], str) and re.fullmatch(r"[a-f0-9]{40}", plan["commit"]), "invalid release commit")
    policy.version(plan["version"])
    require(isinstance(plan["title"], str) and 0 < len(plan["title"]) <= 256, "invalid release title")
    require(isinstance(plan["body"], str) and len(plan["body"]) <= 32000, "invalid release notes")
    require(isinstance(plan["assets"], list) and 0 < len(plan["assets"]) < 100, "invalid asset list")
    names = set()
    for asset in plan["assets"]:
        require(isinstance(asset, dict) and set(asset) == {"name", "size", "digest", "label"}, "invalid asset fields")
        name = asset["name"]
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name), "invalid asset name")
        require(name not in names, "duplicate asset name")
        names.add(name)
        require(positive_integer(asset["size"]), "invalid asset size")
        require(isinstance(asset["digest"], str) and re.fullmatch(r"sha256:[a-f0-9]{64}", asset["digest"]), "invalid asset digest")
        require(isinstance(asset["label"], str), "invalid asset label")
    require(project.archive_prefix + "-" + plan["version"] + ".zip" in names, "release archive is missing")
    return plan


class GitHub:
    def __init__(self, token, repository=REPOSITORY):
        require(repository == AUTOMATION_REPOSITORY or repository in {p.repository for p in PROJECTS.values()},
                "unexpected API repository")
        require(bool(token), "repository-scoped token is required")
        self.token = token
        self.repository = repository
        self.prefix = "/repos/" + repository

    def request(self, path, method="GET", payload=None):
        # Paths are constructed locally. Authenticated redirects are forbidden.
        require(path.startswith(self.prefix + "/") or path == self.prefix, "unexpected GitHub API path")
        if method == "PATCH":
            require(self.repository != AUTOMATION_REPOSITORY
                    and re.fullmatch(re.escape(self.prefix) + r"/releases/[1-9][0-9]*", path)
                    and isinstance(payload, dict)
                    and set(payload) == {"draft", "make_latest", "tag_name", "target_commitish"}
                    and payload["draft"] is False and payload["make_latest"] == "true"
                    and isinstance(payload["tag_name"], str)
                    and re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", payload["tag_name"])
                    and isinstance(payload["target_commitish"], str)
                    and re.fullmatch(r"[a-f0-9]{40}", payload["target_commitish"]),
                    "only publication of an exact prepared draft is allowed")
        elif method == "DELETE":
            require(self.repository == AUTOMATION_REPOSITORY
                    and path in {PLAN_PREFIX + "/actions/variables/" + p.plan_variable for p in PROJECTS.values()}
                    and payload is None,
                    "only the completed authorization may be deleted")
        else:
            require(method == "GET" and payload is None, "unsupported GitHub operation")
            if self.repository == AUTOMATION_REPOSITORY:
                require(path in {PLAN_PREFIX + "/actions/variables/" + p.plan_variable for p in PROJECTS.values()},
                        "plan token may only read configured authorization variables")
            else:
                require("/actions/variables/" not in path, "release token may not access plan variables")
        url = API + path
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Omarchy-Release",
                   "Authorization": "Bearer " + self.token, "Cache-Control": "no-cache",
                   "X-GitHub-Api-Version": "2026-03-10"}
        data = None if payload is None else json.dumps(payload).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.build_opener(policy.NoRedirect()).open(request, timeout=20) as response:
                if method == "DELETE":
                    require(response.status == 204 and response.geturl() == url, "unexpected GitHub delete response")
                    return None
                require(response.status == 200 and response.geturl() == url, "unexpected GitHub response")
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            # Do not log authenticated requests, remote error bodies or the token.
            status = error.code
            error.close()
            raise Blocked(f"GitHub API returned HTTP {status} for {method} {path}") from None
        require(len(raw) <= 2 * 1024 * 1024, "GitHub response is too large")
        return json.loads(raw)

    def get(self, path):
        return self.request(self.prefix + path)


class Publisher:
    def __init__(self, github, plans, catalog=None, project=SCRATCHPEEK):
        self.github = github
        self.plans = plans
        self.project = project
        self.prefix = "/repos/" + project.repository
        self.catalog = catalog or (lambda: policy.read_json(policy.CATALOG_URL, 32 * 1024 * 1024))

    def authorized(self, plan):
        # Re-read authorization instead of relying on an old workflow snapshot.
        value = self.plans.get("/actions/variables/" + self.project.plan_variable)
        require(value.get("name") == self.project.plan_variable and parse_plan(value["value"], self.project) == plan,
                "release authorization changed or was withdrawn")

    def tag_commit(self, tag):
        ref = self.github.get("/git/ref/tags/" + tag)
        require(ref.get("ref") == "refs/tags/" + tag, "unexpected release tag")
        obj = ref.get("object", {})
        # Support both lightweight and annotated tags; bound recursive peeling.
        for _ in range(5):
            require(isinstance(obj, dict) and isinstance(obj.get("sha"), str)
                    and re.fullmatch(r"[a-f0-9]{40}", obj["sha"]), "invalid tag object")
            if obj.get("type") == "commit":
                return obj["sha"]
            require(obj.get("type") == "tag", "release tag does not resolve to a commit")
            obj = self.github.get("/git/tags/" + obj["sha"]).get("object", {})
        raise Blocked("release tag nesting is too deep")

    def check_release(self, plan, release, published=False, allow_text_changes=False):
        require(isinstance(release, dict), "invalid release response")
        require(release.get("id") == plan["releaseId"] and type(release.get("id")) is int, "wrong draft ID")
        require(release.get("tag_name") == "v" + plan["version"], "release tag changed")
        require(release.get("target_commitish") == plan["commit"], "draft must target the exact full commit SHA")
        if not allow_text_changes:
            require(release.get("name") == plan["title"] and release.get("body") == plan["body"], "release text changed")
        require(release.get("prerelease") is False, "prereleases are not eligible")
        require(release.get("draft") is (not published), "unexpected publication state")
        require(release.get("author", {}).get("id") == self.project.author_id, "draft must be prepared by Sarr77")
        require(not release.get("discussion_url"), "release has an unapproved discussion")
        if published:
            require(release.get("immutable") is True, "published release is not immutable")
            require(release.get("html_url") == f"https://github.com/{self.project.repository}/releases/tag/v{plan['version']}",
                    "unexpected published release location")
        else:
            require(release.get("immutable") is False, "unexpected draft immutability")
        assets = self.github.get(f"/releases/{plan['releaseId']}/assets?per_page=100")
        require(isinstance(assets, list) and len(assets) == len(plan["assets"]), "release attachments changed")
        actual = []
        for asset in assets:
            require(isinstance(asset, dict) and asset.get("state") == "uploaded", "asset is not fully uploaded")
            require(type(asset.get("size")) is int, "invalid uploaded asset size")
            require(asset.get("label") is None or isinstance(asset.get("label"), str), "invalid uploaded asset label")
            actual.append({key: asset.get(key) if key != "label" else (asset.get(key) if asset.get(key) is not None else "")
                           for key in ("name", "size", "digest", "label")})
        require(sorted(actual, key=lambda a: a["name"]) == sorted(plan["assets"], key=lambda a: a["name"]),
                "release attachment name, size, label or SHA-256 changed")

    def check_source(self, plan):
        repo = self.github.get("")
        require(repo.get("full_name") == self.project.repository and repo.get("private") is False
                and repo.get("visibility") == "public" and repo.get("default_branch") == "main",
                "publication requires the configured public repository on main")
        require(self.tag_commit("v" + plan["version"]) == plan["commit"], "release tag commit changed")
        content = self.github.get("/contents/manifest.json?ref=" + plan["commit"])
        require(content.get("encoding") == "base64" and content.get("type") == "file", "invalid release manifest")
        manifest = json.loads(base64.b64decode(content["content"]))
        require(manifest.get("id") == self.project.plugin_id and manifest.get("version") == plan["version"]
                and manifest.get("author") == self.project.author_name, "release manifest mismatch")

    def check_latest(self, plan):
        latest = self.github.get("/releases/latest")
        require(latest.get("draft") is False and latest.get("prerelease") is False, "invalid latest stable release")
        tag = latest.get("tag_name", "")
        require(isinstance(tag, str) and tag.startswith("v"), "invalid latest stable tag")
        require(policy.version(tag[1:]) < policy.version(plan["version"]),
                "release is not newer than the latest stable release")

    def disarm(self, plan):
        # Do not delete a different queued authorization. Only one draft should
        # be armed at a time; new plans are armed after this run has finished.
        self.authorized(plan)
        self.plans.request(PLAN_PREFIX + "/actions/variables/" + self.project.plan_variable, method="DELETE")

    def run(self, plan, publish=False):
        self.authorized(plan)
        path = f"/releases/{plan['releaseId']}"
        release = self.github.get(path)
        self.check_source(plan)
        if release.get("draft") is False:
            # A retry after a lost PATCH response must not republish or edit it.
            self.check_release(plan, release, published=True, allow_text_changes=True)
            if publish:
                self.disarm(plan)
            return "already-published"
        self.check_release(plan, release)
        try:
            approved = policy.approved_commit(self.catalog(), self.project)
        except policy.UnverifiedUpdate:
            return "waiting-for-marketplace"
        if approved != plan["commit"]:
            return "waiting-for-marketplace"
        require(self.github.get("/immutable-releases").get("enabled") is True,
                "enable immutable releases before arming publication")
        self.check_latest(plan)
        if not publish:
            return "ready"
        # Fresh checks immediately before publication. No polling loop, retries
        # of PATCH, tag edits, uploads or creation of extra releases.
        self.check_release(plan, self.github.get(path))
        require(self.tag_commit("v" + plan["version"]) == plan["commit"], "release tag changed before publication")
        require(self.github.get("/immutable-releases").get("enabled") is True, "immutability setting changed")
        self.check_latest(plan)
        require(policy.approved_commit(self.catalog(), self.project) == plan["commit"], "marketplace approval changed")
        self.authorized(plan)
        # Explicitly preserve the approved tag and target. Never edit title or
        # notes, including when recovering from a lost publication response.
        result = self.github.request(self.prefix + path, method="PATCH", payload={
            "draft": False, "make_latest": "true", "tag_name": "v" + plan["version"],
            "target_commitish": plan["commit"]})
        self.check_release(plan, result, published=True)
        self.check_release(plan, self.github.get(path), published=True)
        require(self.tag_commit("v" + plan["version"]) == plan["commit"], "published tag mismatch")
        require(self.github.get("/releases/latest").get("id") == plan["releaseId"], "release did not become latest")
        self.disarm(plan)
        return "published"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=sorted(PROJECTS), default="scratchpeek")
    parser.add_argument("--publish", action="store_true", help="publish the authorized draft; default only checks")
    parser.add_argument("--plan", type=Path, help="read a local reviewed plan instead of the environment")
    args = parser.parse_args()
    project = PROJECTS[args.project]
    raw = args.plan.read_text(encoding="utf-8") if args.plan else os.environ.get(project.plan_variable, "")
    if not raw.strip():
        print("disarmed: no authorized release plan")
        return 0
    try:
        plan = parse_plan(raw, project)
        releases = GitHub(os.environ.get(project.token_secret, ""), project.repository)
        plans = GitHub(os.environ.get("RELEASE_PLAN_TOKEN", ""), AUTOMATION_REPOSITORY)
        result = Publisher(releases, plans, project=project).run(plan, args.publish)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        # The exception messages never include tokens, plan text or API bodies.
        print("blocked: " + (str(error) if isinstance(error, Blocked) else type(error).__name__), file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
