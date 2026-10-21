import pytest

from tools import repo_checks

# Every fake credential is assembled at runtime, so this file passes the repo-wide scan itself.
HEX32 = "0123456789abcdef" * 2
PAT = "dapi" + HEX32


@pytest.mark.parametrize("token", [PAT, PAT + "-2"])
def test_flags_databricks_personal_access_tokens(token):
    findings = repo_checks.find_secrets(f"DATABRICKS_TOKEN={token}\n", path=".env")

    assert [finding.where for finding in findings] == [".env:1"]


@pytest.mark.parametrize("secret", ["dose" + HEX32, "dose" + HEX32 + "abcd"])
def test_flags_databricks_oauth_secrets(secret):
    findings = repo_checks.find_secrets(f'client_secret = "{secret}"\n', path="config.py")

    assert [finding.where for finding in findings] == ["config.py:1"]


@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghu_", "ghs_", "ghr_"])
def test_flags_github_tokens(prefix):
    findings = repo_checks.find_secrets(f"export GH_TOKEN={prefix}{'A1b2C3' * 6}\n", path="setup.sh")

    assert [finding.where for finding in findings] == ["setup.sh:1"]


def test_flags_fine_grained_github_tokens():
    token = "github_pat_" + "A1b2C3" * 3 + "Zz9Y" + "_" + "x9Y8" * 14 + "Qrs"  # 22 chars, "_", 59 chars
    assert len(token) == len("github_pat_") + 82

    findings = repo_checks.find_secrets(f"token: {token}\n", path="ci.yml")

    assert [finding.where for finding in findings] == ["ci.yml:1"]


def key_header(kind, word="PRIVATE KEY"):
    return "-----" + "BEGIN " + kind + word + "-----"


@pytest.mark.parametrize("kind", ["", "RSA ", "EC ", "OPENSSH ", "ENCRYPTED "])
def test_flags_private_key_blocks(kind):
    findings = repo_checks.find_secrets(f"{key_header(kind)}\nMIIBVgIBADANBgkq\n", path="key.pem")

    assert [finding.where for finding in findings] == ["key.pem:1"]


JWT = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0", "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"])


def test_flags_json_web_tokens():
    findings = repo_checks.find_secrets(f"session={JWT}\n", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]


BEARER_TOKEN = "Ab12Cd34Ef56Gh78Ij90" * 2


def test_flags_bearer_headers_carrying_a_real_looking_token():
    findings = repo_checks.find_secrets(f'curl -H "Authorization: Bearer {BEARER_TOKEN}" https://x.example\n', path="run.sh")

    assert [finding.where for finding in findings] == ["run.sh:1"]


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Bearer ${DATABRICKS_TOKEN}",
        "Authorization: Bearer ${DATABRICKS_WORKSPACE_ACCESS_TOKEN}",
        "Authorization: Bearer <your-personal-access-token-goes-here>",
        "Authorization: Bearer $DATABRICKS_TOKEN",
        "Authorization: Bearer <token>",
        "Authorization: Bearer {token}",
        'header = "Bearer " + token',
        "Bearer authentication is required",
    ],
)
def test_does_not_flag_bearer_placeholders_or_prose(line):
    assert repo_checks.find_secrets(line + "\n", path="README.md") == []


GITHUB_TOKEN = "ghp_" + "A1b2C3" * 6


@pytest.mark.parametrize(
    "line, secret",
    [
        (f"token={PAT}", PAT),
        (f"secret={'dose' + HEX32}", "dose" + HEX32),
        (f"token={GITHUB_TOKEN}", GITHUB_TOKEN),
        (f"session={JWT}", JWT),
        (f"Authorization: Bearer {BEARER_TOKEN}", BEARER_TOKEN),
    ],
)
def test_findings_never_repeat_the_secret_itself(line, secret):
    findings = repo_checks.find_secrets(line + "\n", path="notes.md")

    assert findings
    assert all(secret not in str(finding) for finding in findings)


@pytest.mark.parametrize(
    "line",
    [
        "token=dapi" + HEX32[:31],  # one hex digit short
        "token=dapi" + "g" * 32,  # right length, not hex
        "token=mydapi" + HEX32,  # glued onto a longer word
        "secret=dose" + HEX32[:31],
        "token=ghp_" + "A1b2C3" * 5 + "A1b2C",  # 35 characters, one short
        "token=ghx_" + "A1b2C3" * 6,  # not a GitHub token prefix
        "token=github_pat_" + "A" * 81,  # one short
        key_header("", "CERTIFICATE"),
        key_header("", "PUBLIC KEY"),
        "jwt-like=" + ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0"]),  # two segments only
    ],
)
def test_does_not_flag_near_misses(line):
    assert repo_checks.find_secrets(line + "\n", path="notes.md") == []
