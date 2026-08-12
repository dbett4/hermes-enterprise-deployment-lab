"""Static and behavioral contracts for the AWS ECS/Fargate cloud IaC reference."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "aws-fargate-reference"
SCRIPT = ROOT / "scripts" / "cloud-iac-proof.sh"
PINS = ROOT / "scripts" / "opentofu_asset_pins.py"
DOCS = ROOT / "docs" / "cloud-hybrid-reference.md"
README = INFRA / "README.md"
EXAMPLE = INFRA / "example.enabled.tfvars"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
API_DOCKERFILE = ROOT / "enterprise-api" / "Dockerfile"
COMPOSE = ROOT / "compose.yaml"

TF_FILES = (
    "versions.tf",
    "provider.tf",
    "variables.tf",
    "main.tf",
    "iam.tf",
    "observability.tf",
    "budget.tf",
    "outputs.tf",
)

REQUIRED_RESOURCE_TYPES = (
    "aws_ecs_cluster",
    "aws_ecs_service",
    "aws_ecs_task_definition",
    "aws_lb",
    "aws_lb_listener",
    "aws_lb_target_group",
    "aws_efs_file_system",
    "aws_efs_mount_target",
    "aws_efs_access_point",
    "aws_iam_role",
    "aws_cloudwatch_log_group",
    "aws_cloudwatch_metric_alarm",
    "aws_budgets_budget",
    "aws_security_group",
)

FORBIDDEN_RESOURCE_TYPES = (
    "aws_vpc",
    "aws_nat_gateway",
    "aws_internet_gateway",
    "aws_subnet",
    "aws_vpn_gateway",
    "aws_vpn_connection",
    "aws_dx_connection",
    "aws_dx_gateway",
    "aws_ec2_transit_gateway",
    "aws_route53_zone",
    "aws_route53_record",
    "aws_secretsmanager_secret",
    "aws_secretsmanager_secret_version",
    "aws_ecr_repository",
    "aws_eks_cluster",
    "aws_instance",
)

OPENTOFU_VERSION = "1.12.5"
AWS_PROVIDER_VERSION = "6.58.0"
AMD64_PIN = "a6894d45ae7a17ce83189cce8fe04b5a65f68cefceb62455b5a6a89fa53ab38f"
ARM64_PIN = "e67e9da2b1ddf5050ebee62a584cb826eafe1dfd3827d7ec20899ac62791ed1a"


def _load_pins_module():
    spec = importlib.util.spec_from_file_location("opentofu_asset_pins", PINS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_infra() -> str:
    return "\n".join((INFRA / name).read_text(encoding="utf-8") for name in TF_FILES)


def test_required_cloud_iac_files_exist() -> None:
    for name in TF_FILES:
        assert (INFRA / name).is_file(), f"missing {name}"
    assert EXAMPLE.is_file()
    assert README.is_file()
    assert DOCS.is_file()
    assert SCRIPT.is_file()
    assert PINS.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "cloud-iac-proof.sh must be executable"
    assert PINS.stat().st_mode & 0o111, "opentofu_asset_pins.py must be executable"
    assert (INFRA / ".terraform.lock.hcl").is_file()


def test_opentofu_and_provider_pins_are_exact() -> None:
    versions = (INFRA / "versions.tf").read_text(encoding="utf-8")
    pins = PINS.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'required_version = "= 1.12.5"' in versions
    assert 'version     = "= 6.58.0"' in versions or 'version = "= 6.58.0"' in versions
    assert f'OPENTOFU_VERSION="{OPENTOFU_VERSION}"' in script
    assert AMD64_PIN in pins
    assert ARM64_PIN in pins
    assert "assert_three_way_equality" in pins
    assert "actual == repository_pin == upstream_manifest" in pins


def test_opentofu_pin_helper_behavior() -> None:
    module = _load_pins_module()

    assert module.OPENTOFU_VERSION == OPENTOFU_VERSION
    assert module.asset_arch_from_uname("x86_64") == "amd64"
    assert module.asset_arch_from_uname("amd64") == "amd64"
    assert module.asset_arch_from_uname("aarch64") == "arm64"
    assert module.asset_arch_from_uname("arm64") == "arm64"
    with pytest.raises(ValueError, match="unsupported"):
        module.asset_arch_from_uname("ppc64le")

    assert module.repository_pin_for_arch("amd64") == AMD64_PIN
    assert module.repository_pin_for_arch("arm64") == ARM64_PIN

    with pytest.raises(SystemExit, match="digest mismatch"):
        module.assert_three_way_equality(
            actual=AMD64_PIN,
            repository_pin="0" * 64,
            upstream_manifest=AMD64_PIN,
        )
    module.assert_three_way_equality(
        actual=AMD64_PIN,
        repository_pin=AMD64_PIN,
        upstream_manifest=AMD64_PIN,
    )


def test_opentofu_verify_rejects_wrong_repository_pin(tmp_path: Path) -> None:
    module = _load_pins_module()
    asset = tmp_path / f"tofu_{OPENTOFU_VERSION}_linux_amd64.tar.gz"
    payload = b"not-a-real-opentofu-archive"
    asset.write_bytes(payload)
    actual = hashlib.sha256(payload).hexdigest()
    manifest = tmp_path / "tofu_SHA256SUMS"
    manifest.write_text(
        f"{actual}  tofu_{OPENTOFU_VERSION}_linux_amd64.tar.gz\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="digest mismatch"):
        module.verify_asset_digests(
            asset_path=asset,
            checksum_manifest_path=manifest,
            asset_name=f"tofu_{OPENTOFU_VERSION}_linux_amd64.tar.gz",
            arch="amd64",
        )


def test_enable_deployment_defaults_false() -> None:
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    assert re.search(
        r'variable\s+"enable_deployment"\s*\{[^}]*default\s*=\s*false',
        variables,
        flags=re.DOTALL,
    )
    example = EXAMPLE.read_text(encoding="utf-8")
    assert "enable_deployment" in example
    assert re.search(r"enable_deployment\s*=\s*true", example)


def test_enabled_inputs_fail_closed_before_plan() -> None:
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    required_guards = (
        'var.aws_account_id != "000000000000"',
        'can(regex("^vpc-([0-9a-f]{8}|[0-9a-f]{17})$", var.vpc_id))',
        "length(var.private_subnet_ids) >= 2",
        "length(distinct(var.private_subnet_ids)) == length(var.private_subnet_ids)",
        'can(regex("^subnet-([0-9a-f]{8}|[0-9a-f]{17})$", subnet_id))',
        "length(var.trusted_ingress_cidrs) > 0",
        '!contains(var.trusted_ingress_cidrs, "0.0.0.0/0")',
        '!contains(var.trusted_ingress_cidrs, "::/0")',
        "can(cidrhost(cidr, 0))",
        'can(regex("^arn:aws[a-zA-Z-]*:acm:[a-z0-9-]+:[0-9]{12}:certificate/[A-Za-z0-9-]+$", var.acm_certificate_arn))',
        'can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.read_secret_arn))',
        'can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.write_secret_arn))',
        "var.read_secret_arn != var.write_secret_arn",
        "length(var.notification_email) > 3",
    )
    for guard in required_guards:
        assert guard in variables, f"missing enabled-input guard: {guard}"


def test_enabled_inputs_reject_root_user_and_non_certificate_acm_arn() -> None:
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    assert 'can(regex("^[1-9][0-9]*$", var.container_user))' in variables
    assert re.search(
        r'arn:aws\[a-zA-Z-\]\*:acm:\[a-z0-9-\]\+:\[0-9\]\{12\}:certificate/',
        variables,
    )


def test_api_image_defaults_to_the_same_non_root_uid_as_the_task() -> None:
    dockerfile = API_DOCKERFILE.read_text(encoding="utf-8")
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    assert re.search(r"(?m)^USER\s+65534:65534\s*$", dockerfile)
    assert re.search(
        r'variable\s+"container_user"\s*\{[\s\S]*?default\s*=\s*"65534"',
        variables,
    )


def test_internal_private_ingress_and_no_public_ip() -> None:
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    assert re.search(r"internal\s*=\s*true", main)
    assert "assign_public_ip" in main
    assert re.search(r"assign_public_ip\s*=\s*false", main)
    assert "trusted_ingress_cidrs" in main
    assert re.search(r'protocol\s*=\s*"HTTPS"', main)
    assert "/readyz" in main
    assert "8080" in main


def test_secrets_are_arns_only_no_secret_strings() -> None:
    infra = _read_infra()
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    example = EXAMPLE.read_text(encoding="utf-8")

    assert "read_secret_arn" in variables
    assert "write_secret_arn" in variables
    assert "secret_string" not in infra.lower()
    assert "aws_secretsmanager_secret" not in infra
    assert re.search(r"default\s*=\s*\"[A-Za-z0-9_\-]{16,}\"", variables) is None
    assert "lab-read-token" not in example
    assert "lab-write-token" not in example
    assert "valueFrom" in (INFRA / "main.tf").read_text(encoding="utf-8") or "value_from" in (
        INFRA / "main.tf"
    ).read_text(encoding="utf-8").lower()
    # Secrets block injection, not plaintext env defaults for tokens.
    assert "ENTERPRISE_API_TOKEN" in (INFRA / "main.tf").read_text(encoding="utf-8")
    assert not re.search(
        r'ENTERPRISE_API_TOKEN"\s*:\s*"[^{]',
        (INFRA / "main.tf").read_text(encoding="utf-8"),
    )


def test_task_role_has_no_broad_aws_permissions() -> None:
    iam = (INFRA / "iam.tf").read_text(encoding="utf-8")
    assert "task" in iam.lower()
    assert "execution" in iam.lower()
    # Execution role secret reads must list exact ARNs, never "*".
    assert "secretsmanager:GetSecretValue" in iam
    assert re.search(
        r'[Rr]esources?\s*=\s*\[[^\]]*(read_secret_arn|write_secret_arn)[^\]]*\]',
        iam,
        flags=re.DOTALL,
    )
    assert not re.search(
        r'secretsmanager:GetSecretValue[\s\S]{0,200}Resource\s*=\s*"\*"',
        iam,
    )
    # Task role must not attach AWS managed admin/power policies or "*" Action.
    assert "AdministratorAccess" not in iam
    assert "PowerUserAccess" not in iam
    task_role_section = iam
    if 'name = "${local.name_prefix}-task"' in iam or "task_role" in iam:
        assert "aws_iam_role_policy" in iam or "aws_iam_role_policy_attachment" in iam
    # No inline task policy granting Action = "*".
    assert not re.search(
        r'resource\s+"aws_iam_role_policy"\s+"[^"]*task[^"]*"\s*\{[\s\S]*Action\s*=\s*"\*"',
        iam,
        flags=re.IGNORECASE,
    )
    assert "ecr:GetAuthorizationToken" in iam
    assert "ecr:BatchCheckLayerAvailability" in iam
    assert "ecr:GetDownloadUrlForLayer" in iam
    assert "ecr:BatchGetImage" in iam
    assert "ecr_repository_arn" in iam
    assert 'data "aws_iam_policy_document" "execution_logs"' in iam
    assert 'data "aws_iam_policy_document" "execution_artifacts"' in iam
    assert 'resource "aws_iam_role_policy" "execution_artifacts"' in iam


def test_efs_iam_authorization_and_task_role_are_scoped() -> None:
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    iam = (INFRA / "iam.tf").read_text(encoding="utf-8")

    assert 'iam             = "ENABLED"' in main
    for action in (
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite",
    ):
        assert action in iam
    assert "elasticfilesystem:ClientRootAccess" not in iam
    assert "aws_efs_file_system.actions[0].arn" in iam
    assert "aws_efs_access_point.actions[0].arn" in iam
    assert 'elasticfilesystem:AccessPointArn' in iam
    assert 'resource "aws_iam_role_policy" "task_efs"' in iam


def test_budget_default_and_alert_only_docs() -> None:
    budget = (INFRA / "budget.tf").read_text(encoding="utf-8")
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    docs = DOCS.read_text(encoding="utf-8")

    assert re.search(
        r'variable\s+"monthly_budget_usd"\s*\{[^}]*default\s*=\s*25',
        variables,
        flags=re.DOTALL,
    )
    assert "aws_budgets_budget" in budget
    assert "ACTUAL" in budget
    assert "FORECASTED" in budget
    for text in (readme, docs, budget):
        assert re.search(r"(?i)alert[- ]only|not (a )?hard|not enforcement|notification", text)


def test_required_and_forbidden_resource_types_in_source() -> None:
    infra = _read_infra()
    for resource_type in REQUIRED_RESOURCE_TYPES:
        assert f'resource "{resource_type}"' in infra, f"missing {resource_type}"
    for resource_type in FORBIDDEN_RESOURCE_TYPES:
        assert f'resource "{resource_type}"' not in infra, f"forbidden {resource_type}"


def test_persistence_and_reliability_markers() -> None:
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    assert "encrypted" in main.lower()
    assert "aws_efs_file_system" in main
    assert "aws_efs_mount_target" in main
    assert "aws_efs_access_point" in main
    assert "deployment_circuit_breaker" in main
    assert "rollback" in main.lower()
    assert "minimum_healthy_percent" in main
    assert "100" in main
    assert "maximum_percent" in main
    assert "200" in main
    assert "ACTION_STORE_PATH" in main
    assert "/var/lib/enterprise-api/actions.json" in main
    assert "readonlyrootfilesystem" in main.lower()
    assert "cpu" in main.lower()
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    assert "var.desired_count == 1" in variables
    assert 'var.task_cpu == "256"' in variables
    assert 'var.task_memory == "512"' in variables
    assert "var.monthly_budget_usd <= 25" in variables
    efs_block = re.search(
        r'resource\s+"aws_security_group"\s+"efs"\s*\{(?P<body>[\s\S]*?)\n\}',
        main,
    )
    assert efs_block is not None
    assert re.search(r"egress\s*=\s*\[\]", efs_block.group("body"))


def test_compose_action_store_volume_is_writable_by_nonroot_api() -> None:
    """Copy-up must provide a writable child directory inside a fresh named volume."""
    dockerfile = API_DOCKERFILE.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")
    owned_store = (
        "RUN install -d -o 65534 -g 65534 -m 0750 /var/lib/enterprise-api/store"
    )

    assert "ACTION_STORE_PATH: /var/lib/enterprise-api/store/actions.json" in compose
    assert "enterprise-api-store:/var/lib/enterprise-api" in compose
    assert owned_store in dockerfile
    assert dockerfile.index(owned_store) < dockerfile.index("USER 65534:65534")


def test_security_groups_use_known_empty_egress_and_standalone_rules_only() -> None:
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    for name in ("alb", "efs"):
        block = re.search(
            rf'resource\s+"aws_security_group"\s+"{name}"\s*\{{(?P<body>[\s\S]*?)\n\}}',
            main,
        )
        assert block is not None
        assert "ingress {" not in block.group("body")
        assert "egress {" not in block.group("body")
        assert re.search(r"egress\s*=\s*\[\]", block.group("body"))
    assert 'resource "aws_vpc_security_group_ingress_rule" "alb_https"' in main
    assert 'resource "aws_vpc_security_group_ingress_rule" "efs_from_tasks"' in main


def test_observability_alarms_and_log_retention() -> None:
    obs = (INFRA / "observability.tf").read_text(encoding="utf-8")
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    assert "aws_cloudwatch_log_group" in obs
    assert re.search(
        r'variable\s+"log_retention_days"\s*\{[^}]*default\s*=\s*7',
        variables,
        flags=re.DOTALL,
    )
    assert "HTTPCode_ELB_5XX_Count" in obs or "HTTPCode_Target_5XX_Count" in obs
    assert "TargetResponseTime" in obs
    assert "HealthyHostCount" in obs
    docs = DOCS.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert re.search(r"(?i)subscription confirmation", docs)
    assert re.search(r"(?i)unverified", docs)
    assert re.search(r"(?i)subscription confirmation|unverified", readme)


def test_proof_script_gates_and_no_apply() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    required = (
        "set -euo pipefail",
        'mktemp -d',
        "opentofu_asset_pins.py",
        "OPENTOFU_RELEASE_URL",
        '"$TOFU_BIN" fmt -check -recursive',
        '"$TOFU_BIN" init -backend=false -input=false -lockfile=readonly',
        '"$TOFU_BIN" validate',
        '"$TOFU_BIN" plan',
        "-refresh=false",
        "example.enabled.tfvars",
        "AWS_EC2_METADATA_DISABLED",
        "skip_credentials_validation",
        "skip_metadata_api_check",
        "skip_requesting_account_id",
        '"$TOFU_BIN" show -json',
        "CLOUD_IAC_PROOF_PASS",
        "deployed=false",
        "aws_refresh=false",
        "apply=false",
        "receipt.json",
        ".cloud-iac-proof",
        "expect_validation_failure root_uid_leading_zero",
        "expect_validation_failure malformed_vpc",
        "expect_validation_failure malformed_subnets",
        "expect_validation_failure malformed_read_secret",
        "expect_validation_failure non_certificate_acm",
    )
    for fragment in required:
        assert fragment in script, f"missing proof gate fragment: {fragment}"

    # May mention apply only as a forbidden pattern for grep gates, never as a command.
    assert re.search(r"(?m)^\s*tofu\s+apply\b", script) is None
    assert re.search(r"(?m)^\s*terraform\s+apply\b", script) is None
    assert "grep" in script
    assert re.search(r"tofu apply|terraform apply", script)
    assert 'parsed = json.loads(doc)' in script
    assert '"secretsmanager:GetSecretValue" in actions and "*" in resources' in script
    assert 'after_unknown.get("egress")' in script
    assert 'iam authorization must be ENABLED' in script
    assert 'elasticfilesystem:ClientMount' in script
    assert 'STAGED_RECEIPT=' in script
    assert 'FINAL_COMMAND_LOG=' in script
    assert 'mv -f -- "$STAGED_RECEIPT" "$RECEIPT_PATH"' in script
    assert 'rm -f "$RECEIPT_PATH"' not in script


def test_receipt_exclusions_documented_in_proof() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "AWS_ACCESS_KEY" not in script or "DUMMY" in script or "EXAMPLE" in script or "ASIA" in script or "AKIA" in script
    # Receipt construction must not embed secret ARNs or credential material fields.
    assert "secret_access_key" not in script.lower() or "receipt" in script
    receipt_block_markers = (
        '"deployed"',
        '"aws_refresh"',
        '"aws_account_mutations"',
        '"opentofu_version"',
        '"aws_provider_version"',
        '"enabled_plan_resource_changes"',
        '"planned_desired_count"',
        '"planned_budget_usd"',
        '"planned_internal_alb"',
        '"planned_public_ip"',
        '"planned_encrypted_efs"',
        '"planned_efs_iam_authorization"',
        '"task_efs_policy_source_scoped"',
        '"known_empty_alb_efs_egress"',
        '"planned_log_retention_days"',
        '"artifact_policy_scoped"',
    )
    for marker in receipt_block_markers:
        assert marker in script


def test_ci_executes_and_uploads_native_cloud_iac_proof() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["cloud-iac-proof"]
    steps = job["steps"]

    run_step = next(step for step in steps if step.get("name") == "Run no-apply cloud IaC proof")
    assert "bash ./scripts/cloud-iac-proof.sh" in str(run_step.get("run", ""))

    upload = next(step for step in steps if step.get("name") == "Upload cloud IaC proof artifacts")
    assert upload.get("if") == "always()"
    assert "actions/upload-artifact@" in str(upload.get("uses", ""))
    assert upload.get("with", {}).get("include-hidden-files") is True
    assert upload.get("with", {}).get("if-no-files-found") == "error"
    paths = str(upload.get("with", {}).get("path", ""))
    assert ".cloud-iac-proof/receipt.json" in paths
    assert ".cloud-iac-proof/commands.log" in paths


def test_docs_state_customer_governed_boundaries() -> None:
    readme = README.read_text(encoding="utf-8")
    docs = DOCS.read_text(encoding="utf-8")
    for text in (readme, docs):
        assert re.search(r"(?i)not deployed|blueprint|deployment blueprint", text)
        assert re.search(r"(?i)existing|customer-governed|pre-existing", text)
        for boundary in (
            "VPC",
            "NAT",
            "VPN",
            "Direct Connect",
            "Transit Gateway",
            "Route53",
            "ECR",
            "remote state",
        ):
            assert boundary in text or boundary.replace("-", " ") in text
        assert re.search(r"(?i)single-region|demo persistence|not a production", text)
        assert re.search(r"(?i)alert[- ]only|not (a )?hard|not enforcement", text)
        assert "enable_deployment" in text


def test_provider_skips_for_offline_plan() -> None:
    provider = (INFRA / "provider.tf").read_text(encoding="utf-8")
    assert "skip_credentials_validation" in provider
    assert "skip_metadata_api_check" in provider
    assert "skip_requesting_account_id" in provider


def test_example_tfvars_use_placeholder_arns_and_digest() -> None:
    example = EXAMPLE.read_text(encoding="utf-8")
    assert "@sha256:" in example
    digest = re.search(r"@sha256:([0-9a-f]{64})", example)
    assert digest is not None
    assert "arn:aws:acm:" in example
    assert "arn:aws:secretsmanager:" in example
    assert "arn:aws:ecr:" in example
    assert "notification_email" in example
    assert "@example.com" in example or "example.invalid" in example

def test_ci_defaults_every_job_to_read_only_repository_permissions() -> None:
    """Public-repository proof jobs must not inherit a write-capable token."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))

    assert workflow.get("permissions") == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}, (
            f"{name} must not expand the workflow's read-only token permissions"
        )
