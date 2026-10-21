"""Checks that keep internal material, secrets and wrong commit identities out of pushes."""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class Finding(NamedTuple):
    check: str  # which check raised it
    where: str  # "path:line", "commit <sha>" or similar
    what: str  # what matched

    def __str__(self):
        return f"{self.where}: [{self.check}] {self.what}"


INTERNAL_REFERENCES = [
    ("Slack workspace link", re.compile(r"databricks\.slack\.com", re.IGNORECASE)),
    ("Jira site link", re.compile(r"databricks\.atlassian\.net", re.IGNORECASE)),
    ("Glean site link", re.compile(r"glean\.com", re.IGNORECASE)),
    ("internal tech docs link", re.compile(r"tech-docs\.dev\.databricks\.com", re.IGNORECASE)),
    # "[/]" keeps this source file from containing the literal repo path, so the repo scan doesn't flag itself.
    ("internal GitHub repo", re.compile(r"databricks-eng[/]universe", re.IGNORECASE)),
    (
        "Google Docs/Sheets/Slides link",
        re.compile(r"docs\.google\.com/(?:document|spreadsheets|presentation)/d/[\w-]+", re.IGNORECASE),
    ),
    ("internal go-link", re.compile(r"(?:(?<![\w./-])|(?<=://))go/(?!no-?go\b)[\w-]+")),
    # Only the known internal projects: a generic upper-case key pattern would also hit things like SHA-256.
    ("internal Jira key", re.compile(r"\b(?:AUTH|FEIP)-\d+\b")),
    ("internal-only tag", re.compile(r"\[INTERNAL\]")),
]

# Lines that may contain an internal reference, as (file path, exact line text). Line numbers play no part.
# The tag is split in two so this file doesn't contain it literally and flag itself.
ALLOWED_LINES = {
    (
        ".claude/agents/director.md",
        "- Back claims with evidence (file path, URL, issue number, or Glean doc title) and a confidence tag: "
        "CONFIRMED, LIKELY, or UNVERIFIED. Mark internal-only sources [INTER" "NAL].",
    ),
}


def _scan(text, path, check, rules, allowed_lines=(), allowed_matches=(), redact=False):
    """Run every (label, pattern) rule over each line of text.

    Skips allowlisted (path, line) pairs and matches that equal an allowlisted value exactly.
    """
    findings = []
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.rstrip("\r")
        if (path, line) in allowed_lines:
            continue
        for label, pattern in rules:
            for match in pattern.finditer(line):
                matched = match.group(0)
                if matched.lower() in allowed_matches:
                    continue
                if redact:  # a report must never repeat a secret in full
                    matched = f"{matched[:4]}... ({len(matched)} chars)"
                findings.append(Finding(check, f"{path}:{number}", f"{label}: {matched}"))
    return findings


def find_internal_references(text, path="<text>"):
    return _scan(text, path, "internal-reference", INTERNAL_REFERENCES, ALLOWED_LINES)


SECRETS = [
    ("Databricks personal access token", re.compile(r"(?<![A-Za-z0-9])dapi[0-9a-f]{32}(?:-\d)?")),
    ("Databricks OAuth secret", re.compile(r"(?<![A-Za-z0-9])dose[0-9a-f]{32,}")),
    ("GitHub token", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,}")),
    ("GitHub fine-grained token", re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{82}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON web token", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # 20+ token characters right after "Bearer": placeholders like ${VAR} or <token> don't qualify.
    ("Bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
]


def find_secrets(text, path="<text>"):
    return _scan(text, path, "secret", SECRETS, redact=True)


# A workspace host is one or more DNS labels in front of one of these domains. Wildcards and placeholders such as
# "*.<domain>" or "<workspace>.<domain>" have no real label there, so they don't match.
HOST_DOMAINS = r"(?:cloud\.databricks\.com|gcp\.databricks\.com|azuredatabricks\.net|(?:staging|dev)\.databricks\.com)"
HOSTNAMES = [("workspace host", re.compile(r"(?:[A-Za-z0-9-]+\.)+" + HOST_DOMAINS, re.IGNORECASE))]
ALLOWED_HOSTS = {"accounts.cloud.databricks.com", "company-workspace.cloud.databricks.com"}


def find_workspace_hostnames(text, path="<text>"):
    return _scan(text, path, "hostname", HOSTNAMES, allowed_matches=ALLOWED_HOSTS)


REQUIRED_GITIGNORE_ENTRIES = [".omc/", ".isaac/", ".build/", "Antigravity Plugin Marketplace Publishing Guide.pdf"]


def find_missing_gitignore_entries(gitignore_text):
    lines = {line.strip() for line in gitignore_text.split("\n")}
    return [
        Finding("gitignore", ".gitignore", f"missing entry: {entry}")
        for entry in REQUIRED_GITIGNORE_ENTRIES
        if entry not in lines
    ]


ALLOWED_EMAIL = "datasciencemonkey@gmail.com"


class Commit(NamedTuple):
    sha: str
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    message: str


def find_bad_commit_identities(commits):
    """Flag the author and the committer separately; Co-authored-by trailers in the message don't count."""
    findings = []
    for commit in commits:
        for role, email in (("author", commit.author_email), ("committer", commit.committer_email)):
            if email != ALLOWED_EMAIL:
                problem = f"{role} email {email} is not {ALLOWED_EMAIL}"
                findings.append(Finding("identity", f"commit {commit.sha[:7]}", problem))
    return findings


def scan_commit_messages(commits):
    """Commit messages get the internal-reference and secret checks."""
    findings = []
    for commit in commits:
        where = f"commit {commit.sha[:7]} message"
        findings += find_internal_references(commit.message, path=where)
        findings += find_secrets(commit.message, path=where)
    return findings


def read_commits(root, base="origin/main"):
    """The commits in base..HEAD, newest first. Raises RuntimeError if base is missing (fail closed, don't skip)."""
    base_found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", base], cwd=root, capture_output=True)
    if base_found.returncode != 0:
        raise RuntimeError(
            f"cannot find {base}, so the commit range can't be checked; fetch it with: git fetch origin main"
        )
    log_format = "%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B"
    log = subprocess.run(
        ["git", "log", "-z", "--no-show-signature", f"--format={log_format}", f"{base}..HEAD"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return [Commit(*record.split("\x1f", 5)) for record in log.split("\0") if record]


def scan_text(text, path="<text>"):
    """All three text checks: internal references, secrets and workspace hostnames."""
    return find_internal_references(text, path) + find_secrets(text, path) + find_workspace_hostnames(text, path)


def scan_files(root):
    """Scan every file git would commit: tracked plus untracked, minus the ignored ones."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    findings = []
    for name in listing.decode().split("\0"):
        path = Path(root) / name
        if name and path.is_file():  # tracked files deleted from the working tree are still listed
            data = path.read_bytes()
            if b"\0" not in data[:8000]:  # binary files are skipped, like git does when diffing
                findings += scan_text(data.decode("utf-8", errors="replace"), path=name)
    return findings


def scan_repo(root):
    """Everything the push gate checks: files, .gitignore, and the commits in origin/main..HEAD."""
    gitignore = Path(root) / ".gitignore"
    gitignore_text = gitignore.read_text() if gitignore.exists() else ""
    findings = scan_files(root) + find_missing_gitignore_entries(gitignore_text)
    try:
        commits = read_commits(root)
    except RuntimeError as problem:
        return findings + [Finding("commit-range", "origin/main", str(problem))]
    return findings + find_bad_commit_identities(commits) + scan_commit_messages(commits)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Keep internal material, secrets and wrong commit identities out of pushes."
    )
    parser.add_argument(
        "--stdin", action="store_true", help="check text piped in (for example PR or issue text) instead of the repo"
    )
    args = parser.parse_args(argv)
    if args.stdin:
        findings = scan_text(sys.stdin.read(), path="<stdin>")
    else:
        toplevel = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        root = toplevel.stdout.strip()
        findings = scan_repo(root)
    if not findings:
        print("repo checks: no findings")
        return 0
    print(f"repo checks: {len(findings)} finding(s)")
    for finding in findings:
        print(f"  {finding}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
