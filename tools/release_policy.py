"""Read exact Omarchy verification snapshots for release publication.

Derived from ScratchPeek's commit-bound catalog checks. No installation code.
"""
import json
import re
import urllib.request

from projects import SCRATCHPEEK

CATALOG_URL = "https://plugins.omarchy.org/catalog.json"

class UnverifiedUpdate(ValueError):
    """A release exists, but it is not eligible for automatic installation."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Neither trust authority may redirect the check to a different source.
        return None


def read_json(url, limit):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "Cache-Control": "no-cache",
        "User-Agent": "Omarchy-Release"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("unexpected metadata response")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("metadata response too large")
    return json.loads(raw)


def approved_commit(catalog, project=SCRATCHPEEK):
    """Use the marketplace's canonical projection, never our own release's SHA.

    Snapshot verification remains valid when unrelated upstream HEAD changes.
    The marketplace resolves baseline policy, maintainer review and revocation;
    this consumer checks the exact repository, plugin and snapshot binding.
    """
    if (not isinstance(catalog, dict) or type(catalog.get("stateSchemaVersion")) is not int
            or catalog["stateSchemaVersion"] != 2 or not isinstance(catalog.get("plugins"), list)):
        raise ValueError("unsupported marketplace catalog")
    matches = [p for p in catalog["plugins"] if isinstance(p, dict) and p.get("id") == project.plugin_id]
    if len(matches) != 1:
        raise UnverifiedUpdate("marketplace listing missing or ambiguous")
    entry = matches[0]
    commit = entry.get("listingValidatedCommit", "")
    if (entry.get("repo") != "https://github.com/" + project.repository or entry.get("sourceType") != "community"
            or entry.get("builtIn") or entry.get("placeholder")
            or entry.get("repositoryLayout") != "root-plugin"
            or entry.get("manifestPath") != "manifest.json"
            or entry.get("installAvailable") is not True
            or entry.get("verificationSnapshotStatus") != "verified"
            or not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit)
            or entry.get("verificationCommit") != commit):
        raise UnverifiedUpdate("marketplace has no verified installable snapshot")
    return commit


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise ValueError("not a stable version")
    return tuple(map(int, value.split(".")))
