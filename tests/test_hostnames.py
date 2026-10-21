import pytest

from tools import repo_checks

# Hosts are assembled at runtime, so this file passes the repo-wide scan itself.
# The two allowlisted example hosts may be written out; look-alikes must be built from them.
ACCOUNTS_HOST = "accounts.cloud.databricks.com"
EXAMPLE_HOST = "company-workspace.cloud.databricks.com"
AWS_HOST = "dbc-1a2b3c4d-e5f6" + ".cloud.databricks.com"
GCP_HOST = "1234567890123456.7" + ".gcp.databricks.com"
AZURE_HOST = "adb-1234567890123456.18." + "azuredatabricks.net"  # multi-part


def test_flags_a_real_aws_workspace_host_with_its_location():
    findings = repo_checks.find_workspace_hostnames(f"host: https://{AWS_HOST}/api\n", path="app.yaml")

    assert [finding.where for finding in findings] == ["app.yaml:1"]
    assert AWS_HOST in findings[0].what


def test_flags_a_real_gcp_workspace_host():
    findings = repo_checks.find_workspace_hostnames(f"host: https://{GCP_HOST}\n", path="app.yaml")

    assert [finding.where for finding in findings] == ["app.yaml:1"]
    assert GCP_HOST in findings[0].what


def test_flags_a_real_multi_part_azure_workspace_host():
    findings = repo_checks.find_workspace_hostnames(f"host: https://{AZURE_HOST}\n", path="app.yaml")

    assert [finding.where for finding in findings] == ["app.yaml:1"]
    assert AZURE_HOST in findings[0].what


@pytest.mark.parametrize("suffix", ["staging.databricks.com", "dev.databricks.com"])
def test_flags_real_staging_and_dev_workspace_hosts(suffix):
    host = "e2-demo-ws." + suffix

    findings = repo_checks.find_workspace_hostnames(f"host: https://{host}\n", path="app.yaml")

    assert [finding.where for finding in findings] == ["app.yaml:1"]
    assert host in findings[0].what


@pytest.mark.parametrize("host", [ACCOUNTS_HOST, EXAMPLE_HOST])
def test_allows_exactly_the_two_public_example_hosts(host):
    assert repo_checks.find_workspace_hostnames(f"login at https://{host}/login\n", path="README.md") == []


@pytest.mark.parametrize(
    "line",
    [
        "match *.cloud.databricks.com",
        "https://<workspace>.cloud.databricks.com/api",
        "https://{workspace}.cloud.databricks.com/api",
        "https://<workspace>/ai-gateway/mcp-services/system.ai.genie_one_mcp",
        "https://<host>/api/2.0/mcp/genie",
        "https://${DATABRICKS_HOST}/api",
        "the cloud.databricks.com domain",
        "docs live at https://docs.databricks.com/en/index.html",
    ],
)
def test_does_not_flag_wildcards_placeholders_or_non_workspace_hosts(line):
    assert repo_checks.find_workspace_hostnames(line + "\n", path="README.md") == []


@pytest.mark.parametrize("host", ["x." + ACCOUNTS_HOST, "prod-" + EXAMPLE_HOST])
def test_allowlist_is_exact_so_look_alike_hosts_are_still_flagged(host):
    findings = repo_checks.find_workspace_hostnames(f"https://{host}/\n", path="README.md")

    assert len(findings) == 1
    assert host in findings[0].what


def test_reports_every_host_on_a_line():
    findings = repo_checks.find_workspace_hostnames(f"from {AWS_HOST} to {GCP_HOST}\n", path="README.md")

    assert len(findings) == 2
