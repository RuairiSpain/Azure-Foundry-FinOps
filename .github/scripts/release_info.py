"""Check a release tag against the code and write the GitHub release notes from the changelog.

    python .github/scripts/release_info.py v0.2.0 --notes-out release-notes.md

Fails (non-zero exit, message on stderr) when the tag isn't a valid version, doesn't match
``foundry_finops._version.__version__``, or ``CHANGELOG.md`` has no non-empty section for that version.
Prints ``version=...`` and ``prerelease=true|false`` lines, ready to append to ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG = re.compile(r"v(\d+\.\d+\.\d+)((?:a|b|rc)\d+|\.post\d+|\.dev\d+)?")


def package_version(version_file: Path) -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version_file.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"Could not read __version__ from {version_file}")
    return match.group(1)


def changelog_section(changelog: Path, version: str) -> str:
    """Body of the ``## <version> ...`` section (up to the next ``## `` heading)."""
    lines = changelog.read_text(encoding="utf-8").splitlines()
    heading = re.compile(rf"##\s+v?{re.escape(version)}(\s.*)?$")
    for i, line in enumerate(lines):
        if heading.match(line):
            body: list[str] = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                body.append(nxt)
            text = "\n".join(body).strip()
            if not text:
                raise SystemExit(f"The CHANGELOG section for {version} is empty")
            return text
    raise SystemExit(
        f"CHANGELOG.md has no '## {version}' section. Move the Unreleased notes under a heading such as "
        f"'## {version} – YYYY-MM-DD' before tagging."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tag", help="the git tag, e.g. v0.2.0")
    ap.add_argument("--version-file", type=Path, default=ROOT / "foundry-finops/src/foundry_finops/_version.py")
    ap.add_argument("--changelog", type=Path, default=ROOT / "foundry-finops/CHANGELOG.md")
    ap.add_argument("--notes-out", type=Path, required=True)
    args = ap.parse_args(argv)

    match = TAG.fullmatch(args.tag)
    if not match:
        raise SystemExit(f"Tag {args.tag!r} isn't a version tag like v1.2.3, v1.2.3rc1 or v1.2.3.post1")
    version = args.tag[1:]
    declared = package_version(args.version_file)
    if declared != version:
        raise SystemExit(f"Tag {args.tag} doesn't match the package version {declared} in {args.version_file.name}")
    notes = changelog_section(args.changelog, version)
    args.notes_out.write_text(notes + "\n", encoding="utf-8")
    print(f"version={version}")
    print(f"prerelease={'true' if match.group(2) and not match.group(2).startswith('.post') else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
