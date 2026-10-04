#!/usr/bin/env python3
"""Create a local publication plan from reviewed notes and an exact Git archive.

This tool has no network access and does not authorize publication by default.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

from publish_verified_release import require, policy, parse_plan
from projects import PROJECTS, SCRATCHPEEK


def package_names(checkout, commit, blob, project):
    """Match the reviewed packager against regular files at the exact commit."""
    tree = subprocess.check_output(["git", "-C", str(checkout), "ls-tree", "-rz", "--full-tree", commit])
    entries = {}
    for record in tree.split(b"\0"):
        if not record:
            continue
        attributes, name = record.split(b"\t", 1)
        mode, _, _ = attributes.split()
        path = name.decode("utf-8")
        require(path not in entries, "invalid release source tree")
        entries[path] = mode.decode("ascii")

    if project.package_layout == "file-list":
        names = blob("tools/package-files.txt").decode("utf-8").splitlines()
    elif project.package_layout == "windowpeek-v1":
        require(hashlib.sha256(blob("tools/package.py")).hexdigest() == project.package_script_sha256,
                "WindowPeek packager changed; review its file rules before preparing a release")
        root_files = {"update.py", "wallpaper_contrast.py", "bounded_io.py", "README.md",
                      "CHANGELOG.md", "LICENSE", "qmldir", ".gitignore", "preview.png"}
        directories = {"docs", "tests", "tools", "vendor", "assets", "translations", ".github"}
        names = [name for name in entries if
                 ("/" not in name and (PurePosixPath(name).suffix in (".qml", ".js", ".json")
                                       or name in root_files))
                 or (name.split("/", 1)[0] in directories and "/" in name
                     and "__pycache__" not in PurePosixPath(name).parts)]
    else:
        raise ValueError("unknown project package layout")

    require(bool(names) and len(names) == len(set(names)), "invalid release file list")
    for name in names:
        path = PurePosixPath(name)
        require(bool(name) and not path.is_absolute() and ".." not in path.parts and path.as_posix() == name,
                "unsafe release file path")
        require(entries.get(name) in ("100644", "100755"), "release source is not a regular committed file")
    return names


def prepare(checkout, commit, archive, title, body, release_id=0, approved=False, checksum=None, project=SCRATCHPEEK):
    require(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit), "expected a full commit SHA")

    def blob(name):
        return subprocess.check_output(["git", "-C", str(checkout), "show", commit + ":" + name])

    require(subprocess.check_output(["git", "-C", str(checkout), "cat-file", "-t", commit]).strip() == b"commit",
            "release source must be a commit object")
    manifest = json.loads(blob("manifest.json"))
    require(manifest.get("id") == project.plugin_id and manifest.get("author") == project.author_name, "wrong source manifest")
    version = manifest["version"]
    policy.version(version)
    require(archive.name == f"{project.archive_prefix}-{version}.zip", "archive filename does not match the version")
    names = package_names(checkout, commit, blob, project)
    raw = archive.read_bytes()
    with zipfile.ZipFile(archive) as content:
        require(sorted(content.namelist()) == sorted(project.key + "/" + name for name in names),
                "archive file list does not match the exact commit")
        for name in names:
            info = content.getinfo(project.key + "/" + name)
            mode = info.external_attr >> 16
            require(not info.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG),
                    "archive entry is not a regular file")
            require(content.read(project.key + "/" + name) == blob(name), "archive differs from the exact commit: " + name)
    digest = hashlib.sha256(raw).hexdigest()
    plan = {"schemaVersion": 1, "approvedForPublication": approved, "repository": project.repository,
            "releaseId": release_id, "commit": commit, "version": version, "title": title, "body": body,
            "assets": [{"name": archive.name, "size": len(raw),
                        "digest": "sha256:" + digest, "label": ""}]}
    if checksum is not None:
        require(checksum.name == f"{project.archive_prefix}-{version}.sha256", "checksum filename does not match the version")
        checksum_bytes = checksum.read_bytes()
        require(checksum_bytes == f"{digest}  {archive.name}\n".encode("ascii"), "checksum contents do not match the archive")
        plan["assets"].append({"name": checksum.name, "size": len(checksum_bytes),
                               "digest": "sha256:" + hashlib.sha256(checksum_bytes).hexdigest(), "label": ""})
    # Validate all remaining fields without treating a review template as armed.
    review = dict(plan, releaseId=release_id or 1, approvedForPublication=True)
    parse_plan(json.dumps(review), project)
    require(type(release_id) is int and release_id >= 0, "invalid draft ID")
    require(not approved or release_id > 0, "an authorized plan needs the actual draft ID")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True,
                        help="local plugin checkout containing the approved commit")
    parser.add_argument("--project", choices=sorted(PROJECTS), default="scratchpeek")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, help="optional reviewed SHA-256 attachment")
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--release-id", type=int, default=0)
    parser.add_argument("--approve-for-publication", action="store_true",
                        help="use only after the owner approves the exact content and destination")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = prepare(args.checkout, args.commit, args.archive, args.title, args.notes.read_text(encoding="utf-8"),
                   args.release_id, args.approve_for_publication, args.checksum, PROJECTS[args.project])
    # Do not silently overwrite an earlier reviewed authorization.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, indent=2)
        stream.write("\n")
    print(args.output)


if __name__ == "__main__":
    main()
