"""Build release notes from the pull requests merged since the previous release tag.

The release workflow gives this the JSON that `gh api --paginate "repos/<owner>/<repo>/pulls?state=closed&base=main"`
prints (a file, or standard input), the OWNER/REPO name, the tag being released and the full SHA of the commit being
released. It lists every pull request whose commit lies in the history of that commit after the previous
v<major>.<minor>.<patch> tag (in all of it when there is no such tag yet) and writes Markdown to a file: for each pull
request its title, its number and the bullets of its "## Summary" section, or just the title when it has none.

When there is a previous tag, the notes end with one line, "Full changelog: <link>", where the link compares the
previous tag with the tag being released on github.com. It is built only from the repository name and the two tag
names, each of which has matched a strict pattern first. The workflow passes the file to `gh release create
--notes-file` and adds nothing else. GitHub's own generated list is left out because it names every merged pull
request, the ones from forks included, which is what the next paragraph keeps out of these notes.

Only some pull requests may put text into the notes: those whose head branch lives in OWNER/REPO itself and whose
author is the repository's owner, a member or a collaborator. Anyone can open a pull request from a fork at the head
commit of a branch we publish, and when we fast-forward main to that commit GitHub marks their pull request merged,
so "merged" alone doesn't say who wrote the title and the description.

Pull request titles and bodies are untrusted text. They are only parsed and written to the notes file: never put on a
command line, never given to a shell. The only command this tool runs is git, as a list of arguments, with a tag or
a commit SHA that matched a strict pattern first. What a pull request can put into the notes is also limited. Its
text becomes single lines (whitespace collapsed, control characters dropped) of bounded length, which cannot start a
heading, quote, list, table, code fence, HTML block or link definition of their own, and only its title and its
Summary bullets are used. Inline Markdown in a bullet (code spans, emphasis, links) is kept on purpose; GitHub
sanitizes what it renders.

Standard library only: the runner runs it with a bare python3.
"""
import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import NamedTuple

# A release tag: v plus a version as tools/release_tag.py accepts it (three numbers, no leading zeros).
TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}")

# What a pull request may contribute. GitHub caps a release body at 125,000 characters; ours stops well short of that.
MAX_BODY = 70_000  # characters of a pull request body that are read (GitHub allows 65,536)
TITLE_MAX = 200
BULLET_MAX = 400
BULLETS_PER_PR = 12
NOTES_MAX = 60_000
FOOTER_ROOM = 300  # kept free for the line that says how many pull requests didn't fit

# The sentence every pull request description ends with. It never belongs in the notes, wherever it ends up.
ATTRIBUTION = re.compile(r"This pull request and its description were written by Isaac\.?", re.IGNORECASE)
HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*))?")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
BULLET = re.compile(r"[ \t]*(?:[-*+]|\d{1,9}[.)])(?:[ \t]+(.*))?")
# What can open a Markdown block at the start of a line: a heading, quote, list item, table or code fence, an HTML
# block or comment (<), or a link reference definition ([), which defines a link for the whole document.
BLOCK_START = re.compile(r"[#>+*=|<\[-]|`{3}|~{3}")
ORDERED = re.compile(r"(\d{1,9})([.)])")

# Whose pull requests may appear (the API's author_association), and what a repository name looks like.
TRUSTED_AUTHORS = ("OWNER", "MEMBER", "COLLABORATOR")
# The owner starts with a letter or digit, and the name is not "." or ".." (the repository ".github" is real), so the
# name can't turn the link into a path.
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/(?!\.{1,2}\Z)[A-Za-z0-9_.-]+")


def check_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError(f"not a full lowercase commit SHA: {str(value)[:60]!r}")


def check_tag(value):
    if not isinstance(value, str) or not TAG.fullmatch(value):
        raise ValueError(f"not a release tag: {str(value)[:40]!r}")


def git(*args, cwd=None):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", check=True
    )
    return result.stdout


def previous_tag(head, cwd=None):
    """The highest v<major>.<minor>.<patch> tag among the tags merged into head, or None if there is none."""
    check_sha(head)
    versions = {}
    for name in git("tag", "--list", "--merged", head, "v*", cwd=cwd).split():
        match = TAG.fullmatch(name)
        if match:
            versions[name] = tuple(int(number) for number in match.groups())
    return max(versions, key=versions.get) if versions else None


def commits_since(tag, head, cwd=None):
    """The full SHAs of the commits in the history of head after tag (all of that history when tag is None)."""
    check_sha(head)
    if tag is None:
        revisions = head
    else:
        check_tag(tag)
        revisions = f"{tag}..{head}"
    return set(git("rev-list", revisions, "--", cwd=cwd).split())


def load_pulls(text):
    """Every item in text, which holds JSON as gh api prints it.

    That is one array, several arrays one after another (--paginate), an array of arrays (--slurp) or one object per
    line. Raises ValueError for anything else.
    """
    decoder = json.JSONDecoder()
    items = []
    position = 0
    try:
        while True:
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text):
                return items
            value, position = decoder.raw_decode(text, position)
            pending = [value]
            while pending:  # flatten nested arrays without recursion, keeping their order
                item = pending.pop()
                if isinstance(item, list):
                    pending.extend(reversed(item))
                else:
                    items.append(item)
    except RecursionError as problem:
        raise ValueError("the JSON is nested too deeply") from problem


def summary_bullets(body):
    """The bullets of the first "Summary" section of a pull request body, as written (one string per bullet).

    The section starts at a Markdown heading called Summary (any level, any case) and ends at the next heading of the
    same or a higher level. Code fences are skipped whole: no heading or bullet inside one counts. A bullet that wraps
    onto the following lines is joined into one, and text that isn't a bullet is ignored. The attribution sentence
    ("This pull request and its description were written by Isaac.") is removed first, so it can't be joined to a bullet.
    """
    if not isinstance(body, str):
        return []
    found = []
    level = None  # the level of the Summary heading, once inside that section
    fence = None  # (character, length) of the code fence being skipped, if any
    current = None  # the bullet being collected

    def finish():
        nonlocal current
        if current:
            found.append(current)
        current = None

    for line in ATTRIBUTION.sub("", body[:MAX_BODY]).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip()
        marker = FENCE.fullmatch(line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1] and not marker.group(2).strip():
                fence = None
            continue
        if marker and not (marker.group(1)[0] == "`" and "`" in marker.group(2)):
            finish()
            fence = (marker.group(1)[0], len(marker.group(1)))
            continue
        heading = HEADING.fullmatch(line)
        if heading:
            finish()
            depth = len(heading.group(1))
            if level is None:
                if (heading.group(2) or "").strip().rstrip("#").strip().lower() == "summary":
                    level = depth
            elif depth <= level:
                break
            continue
        if level is None:
            continue
        bullet = BULLET.fullmatch(line)
        if bullet:
            finish()
            current = (bullet.group(1) or "").strip()
        elif not line:
            finish()
        elif current is not None:
            current = f"{current} {line.strip()}".strip()
    finish()
    return found


def one_line(text, limit):
    """text as one short line that can't start a Markdown block.

    Whitespace of every kind becomes a single space, control and formatting characters are dropped, the line is cut
    at limit characters (ending in an ellipsis), and a leading character that would make a heading, quote, list item,
    table, code fence, HTML block or link definition gets a backslash in front of it.
    """
    kept = []
    for char in text[: limit * 4]:  # more than will be kept, so a huge text isn't walked in full
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char) not in ("Cc", "Cf", "Cs"):
            kept.append(char)
    line = " ".join("".join(kept).split())
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    if BLOCK_START.match(line):
        return "\\" + line
    ordered = ORDERED.match(line)
    if ordered:
        return f"{ordered.group(1)}\\{line[ordered.end(1):]}"
    return line


class Pull(NamedTuple):
    number: int
    title: str
    body: str
    merged_at: str
    commits: tuple  # the SHAs that mark it on main: its merge commit and its head commit


def text_of(value):
    return value if isinstance(value, str) else ""


def read_pull(item, repo):
    """A Pull from one item of the API's JSON, or None if the item may not appear in the notes.

    It must be a merged pull request with a whole number, from a branch of repo itself (a deleted or unknown source
    repository doesn't count), by the owner, a member or a collaborator.
    """
    if not isinstance(item, dict):
        return None
    number, merged_at = item.get("number"), item.get("merged_at")
    if isinstance(number, bool) or not isinstance(number, int) or not isinstance(merged_at, str) or not merged_at:
        return None
    head = item.get("head")
    head = head if isinstance(head, dict) else {}
    source = head.get("repo")
    source = source.get("full_name") if isinstance(source, dict) else None
    if not isinstance(source, str) or source.lower() != repo.lower():  # repository names ignore case
        return None
    if item.get("author_association") not in TRUSTED_AUTHORS:
        return None
    commits = (item.get("merge_commit_sha"), head.get("sha"))
    return Pull(
        number, text_of(item.get("title")), text_of(item.get("body")), merged_at,
        tuple(commit for commit in commits if isinstance(commit, str)),
    )


def render(pull):
    """The lines for one pull request: its title and number, then the bullets of its Summary."""
    title = one_line(pull.title, TITLE_MAX) or "(no title)"
    lines = [f"- {title} (#{pull.number})"]
    bullets = summary_bullets(pull.body)
    shown = (one_line(bullet, BULLET_MAX) for bullet in bullets[:BULLETS_PER_PR])
    lines += [f"  - {bullet}" for bullet in shown if bullet]
    if len(bullets) > BULLETS_PER_PR:
        lines.append(f"  - …and {len(bullets) - BULLETS_PER_PR} more")
    return lines


def build_notes(pulls, shas, repo, previous=None, tag=None):
    """The Markdown for the pull requests in pulls that were merged in the commits in shas, oldest first.

    repo is the OWNER/REPO the pull requests must come from (see read_pull). previous is the release tag the notes
    start after, if any, and tag is the release tag being made now. A pull request counts when its merge commit or its
    head commit is in shas, so merges made on GitHub and fast-forwards both work. Items that aren't merged pull requests
    from repo by someone trusted, and repeats of the same number, are skipped.

    When there is a previous tag and a tag, the notes end with a link that compares them, after a blank line so that it
    isn't read as part of the last list item. Its text is built from repo, previous and tag alone, each checked first.
    """
    if not isinstance(repo, str) or not REPO.fullmatch(repo):
        raise ValueError(f"not an OWNER/REPO name: {str(repo)[:60]!r}")
    if previous is not None:
        check_tag(previous)
    if tag is not None:
        check_tag(tag)
    closing = f"\nFull changelog: https://github.com/{repo}/compare/{previous}...{tag}\n" if previous and tag else ""
    heading = f"## Pull requests since {previous}" if previous else "## Pull requests"
    merged = {}
    for pull in filter(None, (read_pull(item, repo) for item in pulls)):
        if any(commit in shas for commit in pull.commits):
            merged.setdefault(pull.number, pull)
    ordered = sorted(merged.values(), key=lambda pull: (pull.merged_at, pull.number))
    if not ordered:
        nothing = f"No pull requests were merged since {previous}." if previous else "No pull requests have been merged yet."
        return f"{heading}\n\n{nothing}\n{closing}"
    lines = [heading, ""]
    size = len(heading) + 2
    for index, pull in enumerate(ordered):
        entry = render(pull)
        cost = sum(len(line) + 1 for line in entry)
        if size + cost > NOTES_MAX - FOOTER_ROOM - len(closing):  # the link always fits
            lines.append(f"- …and {len(ordered) - index} more pull requests, left out to keep these notes short")
            break
        lines += entry
        size += cost
    return "\n".join(lines) + "\n" + closing


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write release notes: the pull requests merged since the previous release tag, from gh api JSON."
    )
    parser.add_argument("--pulls", default="-", help="the JSON from gh api: a file, or - for standard input (default)")
    parser.add_argument("--head", required=True, help="the full SHA of the commit being released")
    parser.add_argument(
        "--repo", required=True, help="OWNER/REPO: only pull requests from branches of this repository are listed"
    )
    parser.add_argument(
        "--tag", required=True,
        help="the release tag being made now; with a previous release, the notes end with a link comparing the two",
    )
    parser.add_argument("--output", required=True, type=Path, help="the file to write the notes to")
    args = parser.parse_args(argv)
    try:
        check_sha(args.head)
        text = sys.stdin.read() if args.pulls == "-" else Path(args.pulls).read_text(encoding="utf-8")
        pulls = load_pulls(text)
        previous = previous_tag(args.head)
        notes = build_notes(pulls, commits_since(previous, args.head), args.repo, previous, args.tag)
        args.output.write_text(notes, encoding="utf-8")
    except subprocess.CalledProcessError as problem:
        reason = (problem.stderr or "").strip().splitlines()
        print(f"release_notes: git failed: {reason[0][:200] if reason else problem.returncode}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as problem:
        print(f"release_notes: {problem}", file=sys.stderr)
        return 1
    print(f"release_notes: previous tag {previous or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
