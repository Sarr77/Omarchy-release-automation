"""A plan must bind the reviewed archive to exact committed source bytes."""
import importlib.util
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from projects import WINDOWPEEK
spec = importlib.util.spec_from_file_location("release_plan", ROOT / "tools/prepare_release_plan.py")
release_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_plan)


class ReleasePlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scratchpeek-release-plan-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "tools").mkdir()
        (self.root / "manifest.json").write_text(json.dumps({"id": "sarr.scratchpeek", "version": "0.11.2", "author": "Sarr"}))
        self.names = ["manifest.json", "Widget.qml", "tools/package-files.txt"]
        (self.root / "Widget.qml").write_text("Reviewed source\n")
        (self.root / "tools/package-files.txt").write_text("\n".join(self.names) + "\n")
        self.git("init", "--quiet", "--template=", "--initial-branch=main")
        self.git("add", ".")
        self.git("-c", "user.name=Sarr", "-c", "user.email=rocksklep@gmail.com", "-c", "commit.gpgsign=false",
                 "commit", "--quiet", "--no-verify", "-m", "Release test fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        self.archive = self.root / "ScratchPeek-0.11.2.zip"
        self.write_archive()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *args])

    def write_archive(self, extra=None, changed=None):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name in self.names:
                archive.writestr("scratchpeek/" + name, b"changed" if name == changed else (self.root / name).read_bytes())
            if extra:
                archive.writestr(extra, b"unexpected")

    def prepare(self, **kwargs):
        return release_plan.prepare(self.root, self.commit, self.archive, "ScratchPeek 0.11.2", "Reviewed notes\n", **kwargs)

    def test_review_template_is_disarmed_and_approved_plan_requires_id(self):
        result = self.prepare()
        self.assertFalse(result["approvedForPublication"])
        self.assertEqual(result["releaseId"], 0)
        self.assertEqual(result["commit"], self.commit)
        self.assertEqual(result["assets"][0]["size"], self.archive.stat().st_size)
        with self.assertRaises(ValueError): self.prepare(approved=True)
        self.assertEqual(self.prepare(approved=True, release_id=123)["releaseId"], 123)

    def test_changed_archive_content_is_rejected(self):
        self.write_archive(changed="Widget.qml")
        with self.assertRaisesRegex(ValueError, "differs"): self.prepare()

    def test_missing_extra_or_duplicate_files_are_rejected(self):
        for extra in ("scratchpeek/private-note.md", "scratchpeek/Widget.qml"):
            self.write_archive(extra=extra)
            with self.assertRaisesRegex(ValueError, "file list"): self.prepare()
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("scratchpeek/manifest.json", (self.root / "manifest.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "file list"): self.prepare()

    def test_working_tree_changes_do_not_change_commit_bound_plan(self):
        original = self.prepare()
        (self.root / "Widget.qml").write_text("Later local changes")
        self.assertEqual(self.prepare(), original)
        self.write_archive()
        with self.assertRaisesRegex(ValueError, "differs"): self.prepare()

    def test_wrong_archive_version_or_non_commit_ref_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "full commit"):
            release_plan.prepare(self.root, "main", self.archive, "title", "notes")
        wrong = self.root / "ScratchPeek-0.11.3.zip"
        self.archive.rename(wrong)
        with self.assertRaisesRegex(ValueError, "filename"):
            release_plan.prepare(self.root, self.commit, wrong, "title", "notes")

    def test_optional_checksum_is_bound_to_exact_archive_bytes(self):
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        checksum = self.root / "ScratchPeek-0.11.2.sha256"
        checksum.write_text(f"{digest}  {self.archive.name}\n", encoding="ascii")
        plan = self.prepare(checksum=checksum, release_id=123)
        self.assertFalse(plan["approvedForPublication"])
        self.assertEqual(len(plan["assets"]), 2)
        self.assertEqual(plan["assets"][1]["digest"], "sha256:" + hashlib.sha256(checksum.read_bytes()).hexdigest())
        checksum.write_text(f"{'a' * 64}  {self.archive.name}\n")
        with self.assertRaisesRegex(ValueError, "checksum contents"):
            self.prepare(checksum=checksum)

    def test_checksum_with_wrong_filename_is_rejected(self):
        checksum = self.root / "ScratchPeek-0.11.3.sha256"
        checksum.write_text("invalid")
        with self.assertRaisesRegex(ValueError, "checksum filename"):
            self.prepare(checksum=checksum)

    def test_full_sha_must_identify_commit_not_blob(self):
        blob = self.git("rev-parse", self.commit + ":Widget.qml").decode().strip()
        with self.assertRaisesRegex(ValueError, "commit object"):
            release_plan.prepare(self.root, blob, self.archive, "title", "notes")

    def test_archive_symlink_with_matching_contents_is_rejected(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name in self.names:
                info = zipfile.ZipInfo("scratchpeek/" + name)
                info.create_system = 3
                info.external_attr = (0o120777 if name == "Widget.qml" else 0o100644) << 16
                archive.writestr(info, (self.root / name).read_bytes())
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            self.prepare()

    def test_committed_symlink_is_rejected_even_if_archive_bytes_match(self):
        (self.root / "Widget.qml").unlink()
        (self.root / "Widget.qml").symlink_to("manifest.json")
        self.git("add", "Widget.qml")
        self.git("-c", "user.name=Sarr", "-c", "user.email=rocksklep@gmail.com", "-c", "commit.gpgsign=false",
                 "commit", "--quiet", "--no-verify", "-m", "Symlink test fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name in self.names:
                archive.writestr("scratchpeek/" + name, self.git("show", self.commit + ":" + name))
        with self.assertRaisesRegex(ValueError, "regular committed file"):
            self.prepare()


class WindowPeekPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="windowpeek-release-plan-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in ("tools", "docs", ".github/workflows"):
            (self.root / directory).mkdir(parents=True)
        self.files = {
            "manifest.json": json.dumps({"id": "sarr.windowpeek", "version": "0.7.9", "author": "Sarr"}),
            "Widget.qml": "Item {}\n", "update.py": "# updater\n", "README.md": "# WindowPeek\n",
            "README.pl.md": "Not selected by current packager\n",
            "docs/UPDATES.md": "Release details\n", ".github/workflows/test.yml": "name: Test\n",
            "tools/package.py": "# reviewed packager fixture\n",
        }
        for name, body in self.files.items():
            (self.root / name).write_text(body)
        self.project = replace(WINDOWPEEK, package_script_sha256=hashlib.sha256(
            self.files["tools/package.py"].encode()).hexdigest())
        self.git("init", "--quiet", "--template=", "--initial-branch=main")
        self.git("add", ".")
        self.git("-c", "user.name=Sarr", "-c", "user.email=rocksklep@gmail.com", "-c", "commit.gpgsign=false",
                 "commit", "--quiet", "--no-verify", "-m", "Package fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        self.archive = self.root / "WindowPeek-0.7.9.zip"
        self.names = sorted(set(self.files) - {"README.pl.md"})
        self.write_archive()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *args])

    def write_archive(self, changed=None, extra=None):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name in self.names:
                archive.writestr("windowpeek/" + name, b"tampered" if name == changed else self.files[name].encode())
            if extra:
                archive.writestr(extra, b"unexpected")

    def prepare(self):
        return release_plan.prepare(self.root, self.commit, self.archive, "WindowPeek 0.7.9", "Approved notes\n",
                                    project=self.project)

    def test_exact_committed_windowpeek_package_is_accepted_without_a_file_list(self):
        (self.root / "docs/local-only.md").write_text("Not in the commit")
        plan = self.prepare()
        self.assertEqual(plan["repository"], "Sarr77/WindowPeek")
        self.assertEqual(plan["assets"][0]["name"], "WindowPeek-0.7.9.zip")
        self.assertFalse(plan["approvedForPublication"])

    def test_extra_or_changed_package_content_is_blocked(self):
        self.write_archive(changed="Widget.qml")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.prepare()
        self.write_archive(extra="windowpeek/README.pl.md")
        with self.assertRaisesRegex(ValueError, "file list"):
            self.prepare()

    def test_packager_change_or_source_symlink_requires_review(self):
        with self.assertRaisesRegex(ValueError, "packager changed"):
            release_plan.prepare(self.root, self.commit, self.archive, "title", "notes", project=WINDOWPEEK)
        (self.root / "Widget.qml").unlink()
        (self.root / "Widget.qml").symlink_to("manifest.json")
        self.git("add", "Widget.qml")
        self.git("-c", "user.name=Sarr", "-c", "user.email=rocksklep@gmail.com", "-c", "commit.gpgsign=false",
                 "commit", "--quiet", "--no-verify", "-m", "Symlink fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(ValueError, "regular committed file"):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
