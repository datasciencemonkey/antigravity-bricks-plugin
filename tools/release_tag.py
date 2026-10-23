"""Print the release tag for the Databricks Genie plugin: "v" plus the version in its manifest.

The release workflow (.github/workflows/release.yml) runs this to learn which tag the commit it is looking at should
carry. The version rule:

- plugins/databricks-genie/plugin.json is the only source of the version, and the tag is always "v" plus that exact
  string. This tool and the workflow never compute, bump or write a version.
- Each release bumps only the last number: 0.3.0, then 0.3.1, then 0.3.2. A person changes the manifest; the workflow
  only reads it.
- The version must be exactly three numbers separated by dots, with no leading zero in any of them. Anything else
  ("1.0.0; rm -rf /", "v1", "0.3.01", an empty string) prints no tag, says why on stderr and exits with status 1, so
  the workflow stops before it tags anything. (0.3.01 would name the release 0.3.1 under a second name.)
- A release is only made for a version newer than every release tag that exists. With --newer-than-tags the tool
  reads the tag names from standard input (`git tag --list 'v*' | python3 tools/release_tag.py --newer-than-tags`) and
  prints the tag only if the version is above all of them, so a re-run of an old green run can't add a release out of
  order. Names that aren't v plus a version (v1, v1.0.0-rc1, latest) don't count. When the version is not newer it
  prints nothing, says so on stderr and still exits 0: "nothing to release" is an answer, not a failure.

Standard library only: the runner runs it with a bare python3.
"""
import argparse
import json
import re
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "plugins" / "databricks-genie" / "plugin.json"

# Not ^...$ (the $ accepts a trailing newline), and ASCII only (a bare \d also matches digits from other scripts).
# Each number is 0 or starts with 1-9, so 0.3.01 and 0.3.1 can't both be versions. tools/release_notes.py has the same
# rule for the tags it reads.
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)
TAG = re.compile("v" + VERSION.pattern, re.ASCII)


class VersionError(Exception):
    """The manifest can't give a release version."""


def read_version(manifest):
    """The manifest's version, if it is a string of three dot-separated numbers; otherwise raise VersionError."""
    try:
        data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except OSError as problem:
        raise VersionError(f"cannot read {manifest}: {problem.strerror or problem}") from problem
    except ValueError as problem:  # not JSON, or not UTF-8
        raise VersionError(f"{manifest} is not valid JSON") from problem
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str):
        raise VersionError(f'{manifest} has no "version" string')
    if not VERSION.fullmatch(version):
        # repr() keeps a newline in the value from starting a line of its own in the log.
        raise VersionError(
            f"the version {version[:60]!r} in {manifest} is not three numbers without leading zeros, separated by dots "
            "(like 0.3.1)"
        )
    return version


def newest_tag(lines):
    """The highest release tag among the lines (each a tag name), as (major, minor, patch) numbers, or None."""
    found = []
    for line in lines:
        match = TAG.fullmatch(line.strip())  # a stray space or carriage return makes the check stricter, never looser
        if match:
            found.append(tuple(int(number) for number in match.groups()))
    return max(found, default=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the release tag: v plus the version in the plugin manifest.")
    parser.add_argument(
        "manifest", nargs="?", type=Path, default=MANIFEST, help="the manifest to read (default: the Genie plugin's)"
    )
    parser.add_argument(
        "--newer-than-tags", action="store_true",
        help="read the existing tag names from standard input and print the tag only if its version is newer than all "
        "of the release tags among them",
    )
    args = parser.parse_args(argv)
    try:
        version = read_version(args.manifest)
    except VersionError as problem:
        print(f"release_tag: {problem}", file=sys.stderr)
        return 1
    if args.newer_than_tags:
        newest = newest_tag(sys.stdin)
        mine = tuple(int(number) for number in VERSION.fullmatch(version).groups())
        if newest is not None and mine <= newest:
            print(
                f"release_tag: v{version} is not newer than v{'.'.join(map(str, newest))}, a release tag that "
                "exists, so there is nothing to release",
                file=sys.stderr,
            )
            return 0
    print(f"v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
