"""The release workflow's tag/changelog checks (.github/scripts/release_info.py)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "release_info.py"


def load():
    spec = importlib.util.spec_from_file_location("release_info", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANGELOG = """# Changelog

## Unreleased
- not yet

## 0.2.0 – 2026-12-01
- Added a thing.
- Fixed another.

## 0.1.0 – 2026-11-21
- First release.
"""


@unittest.skipUnless(SCRIPT.is_file(), "only runs from a repository checkout")
class ReleaseInfoTests(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.changelog = self.dir / "CHANGELOG.md"
        self.changelog.write_text(CHANGELOG, encoding="utf-8")
        self.version_file = self.dir / "_version.py"
        self.notes = self.dir / "notes.md"

    def run_script(self, tag: str, version: str = "0.2.0") -> str:
        self.version_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.main([tag, "--version-file", str(self.version_file), "--changelog", str(self.changelog),
                           "--notes-out", str(self.notes)])
        return out.getvalue()

    def test_notes_are_the_tagged_section_only(self):
        out = self.run_script("v0.2.0")
        self.assertIn("version=0.2.0", out)
        self.assertIn("prerelease=false", out)
        self.assertEqual(self.notes.read_text(), "- Added a thing.\n- Fixed another.\n")

    def test_tag_must_match_the_package_version(self):
        with self.assertRaisesRegex(SystemExit, "doesn't match the package version"):
            self.run_script("v0.2.1")

    def test_tag_must_look_like_a_version(self):
        for tag in ("0.2.0", "v0.2", "release-1", "v1.2.3-beta"):
            with self.subTest(tag=tag), self.assertRaisesRegex(SystemExit, "isn't a version tag"):
                self.run_script(tag)

    def test_missing_or_empty_changelog_section_fails_with_advice(self):
        with self.assertRaisesRegex(SystemExit, "Move the Unreleased notes"):
            self.run_script("v0.3.0", version="0.3.0")
        self.changelog.write_text("## 0.3.0 – 2026-12-02\n\n## 0.2.0\n- x\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "is empty"):
            self.run_script("v0.3.0", version="0.3.0")

    def test_release_candidates_are_prereleases(self):
        self.changelog.write_text("## 0.3.0rc1 – 2026-12-02\n- candidate\n", encoding="utf-8")
        self.assertIn("prerelease=true", self.run_script("v0.3.0rc1", version="0.3.0rc1"))

    def test_the_real_changelog_and_version_files_are_readable(self):
        self.assertRegex(self.mod.package_version(self.mod.ROOT / "foundry-finops/src/foundry_finops/_version.py"),
                         r"^\d+\.\d+\.\d+")
        self.assertTrue((self.mod.ROOT / "foundry-finops/CHANGELOG.md").is_file())


if __name__ == "__main__":
    unittest.main()
