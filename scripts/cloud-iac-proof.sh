#!/usr/bin/env bash
# Cloud/hybrid IaC proof: repository-pinned OpenTofu format/validate/plan only.
# No tofu apply / terraform apply. No real AWS credentials. No spend.
#
# Downloads and local execution are allowed. Cloud APIs are not called for
# apply or refresh (dummy credentials, provider skips, -refresh=false).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
OPENTOFU_VERSION="1.12.5"
AWS_PROVIDER_VERSION="6.58.0"
PROOF_DIR="${CLOUD_IAC_PROOF_DIR:-${ROOT_DIR}/.cloud-iac-proof}"
RECEIPT_PATH="${PROOF_DIR}/receipt.json"
FINAL_COMMAND_LOG="${PROOF_DIR}/commands.log"
STAGED_RECEIPT="${PROOF_DIR}/.receipt.json.${BASHPID}"
COMMAND_LOG="${PROOF_DIR}/.commands.log.${BASHPID}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-cloud-iac-proof.XXXXXX")"
IAC_SRC="${ROOT_DIR}/infra/aws-fargate-reference"
IAC_DIR="${WORK_DIR}/aws-fargate-reference"

cleanup() {
  rm -f -- "$STAGED_RECEIPT" "$COMMAND_LOG"
  case "$WORK_DIR" in
    /tmp/*|"${TMPDIR:-/tmp}"/*) rm -rf -- "$WORK_DIR" ;;
    *) echo "Refusing to remove unexpected cloud-iac work directory: $WORK_DIR" >&2 ;;
  esac
}
trap cleanup EXIT

fail() {
  echo "CLOUD_IAC_PROOF_FAIL: $*" >&2
  exit 1
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  fail "create .venv and install requirements-dev.txt first"
fi
for command in curl sha256sum tar; do
  if ! command -v "$command" >/dev/null 2>&1; then
    fail "missing required command: $command"
  fi
done

ASSET_ARCH="$("$PYTHON_BIN" "${ROOT_DIR}/scripts/opentofu_asset_pins.py" arch "$(uname -m)")"
ASSET="tofu_${OPENTOFU_VERSION}_linux_${ASSET_ARCH}.tar.gz"
RELEASE_URL="${OPENTOFU_RELEASE_URL:-https://github.com/opentofu/opentofu/releases/download/v${OPENTOFU_VERSION}}"
ASSET_PATH="${WORK_DIR}/${ASSET}"
CHECKSUM_PATH="${WORK_DIR}/tofu_${OPENTOFU_VERSION}_SHA256SUMS"
REPOSITORY_PIN="$("$PYTHON_BIN" "${ROOT_DIR}/scripts/opentofu_asset_pins.py" pin "$ASSET_ARCH")"

mkdir -p "$PROOF_DIR"
# Preserve the last successful receipt/log until every new gate passes.
rm -f -- "$STAGED_RECEIPT" "$COMMAND_LOG"
: >"$COMMAND_LOG"

log_cmd() {
  printf '+ %s\n' "$*" >>"$COMMAND_LOG"
  "$@"
}

curl --fail --silent --show-error --location --retry 3 \
  "${RELEASE_URL}/tofu_${OPENTOFU_VERSION}_SHA256SUMS" -o "$CHECKSUM_PATH"
curl --fail --silent --show-error --location --retry 3 \
  "${RELEASE_URL}/${ASSET}" -o "$ASSET_PATH"

# Require actual == repository_pin == upstream_manifest before extraction.
ACTUAL_SHA256="$("$PYTHON_BIN" "${ROOT_DIR}/scripts/opentofu_asset_pins.py" verify \
  "$ASSET_PATH" "$CHECKSUM_PATH" "$ASSET" "$ASSET_ARCH")"
test "$ACTUAL_SHA256" = "$REPOSITORY_PIN"

tar -xzf "$ASSET_PATH" -C "$WORK_DIR"
TOFU_BIN="${WORK_DIR}/tofu"
test -x "$TOFU_BIN"
TOFU_VERSION_OUT="$("$TOFU_BIN" version | head -n 1)"
printf '%s\n' "$TOFU_VERSION_OUT" | grep -F "OpenTofu v${OPENTOFU_VERSION}" >/dev/null

# Copy IaC into the temporary workspace (no backend, no apply).
mkdir -p "$IAC_DIR"
cp -a "${IAC_SRC}/." "$IAC_DIR/"

PROVIDER_TF="${IAC_DIR}/provider.tf"
grep -F "skip_credentials_validation" "$PROVIDER_TF" >/dev/null
grep -F "skip_metadata_api_check" "$PROVIDER_TF" >/dev/null
grep -F "skip_requesting_account_id" "$PROVIDER_TF" >/dev/null

cd "$IAC_DIR"
log_cmd "$TOFU_BIN" fmt -check -recursive
log_cmd "$TOFU_BIN" init -backend=false -input=false -lockfile=readonly
log_cmd "$TOFU_BIN" validate

# Both plans use dummy credentials, disabled metadata/account checks, and
# -refresh=false. GitHub and the provider registry are contacted only to fetch
# tool/provider packages; no AWS refresh, apply, or account mutation is invoked.
export AWS_ACCESS_KEY_ID="ASIACLOUDIACTESTEXAMPLE"
export AWS_SECRET_ACCESS_KEY="cloudiactestdummysecret"
export AWS_SESSION_TOKEN="cloudiactestdummysession"
export AWS_EC2_METADATA_DISABLED=true
export AWS_DEFAULT_REGION="us-east-1"
# Provider skips already set in provider.tf: skip_credentials_validation,
# skip_metadata_api_check, skip_requesting_account_id.

# Default/disabled plan: zero managed resource changes.
log_cmd "$TOFU_BIN" plan -refresh=false -input=false -out="${WORK_DIR}/disabled.tfplan"
DISABLED_JSON="${WORK_DIR}/disabled.json"
"$TOFU_BIN" show -json "${WORK_DIR}/disabled.tfplan" >"$DISABLED_JSON"

# Enabled offline plan: graph evaluation only.

log_cmd "$TOFU_BIN" plan \
  -var-file=example.enabled.tfvars \
  -refresh=false \
  -input=false \
  -out="${WORK_DIR}/enabled.tfplan"
ENABLED_JSON="${WORK_DIR}/enabled.json"
"$TOFU_BIN" show -json "${WORK_DIR}/enabled.tfplan" >"$ENABLED_JSON"

# Prove consequential input guards through OpenTofu's real variable validator
# without replanning the provider-backed 29-resource graph for every negative.
# This isolated module contains the exact production variables and good tfvars,
# but no resources or providers.
VALIDATION_DIR="${WORK_DIR}/variable-validation"
mkdir -p "$VALIDATION_DIR"
cp variables.tf example.enabled.tfvars "$VALIDATION_DIR/"
: >"${VALIDATION_DIR}/main.tf"

expect_validation_failure() {
  local case_name="$1"
  shift
  local case_log="${WORK_DIR}/${case_name}.log"
  if "$TOFU_BIN" -chdir="$VALIDATION_DIR" plan \
    -var-file=example.enabled.tfvars \
    -refresh=false \
    -input=false \
    "$@" >"$case_log" 2>&1; then
    fail "negative input case unexpectedly validated: ${case_name}"
  fi
  grep -E 'Invalid value for (input )?variable|Invalid function argument' "$case_log" >/dev/null ||
    fail "negative input case failed for an unexpected reason: ${case_name}"
  printf '+ expected validation failure: %s\n' "$case_name" >>"$COMMAND_LOG"
}

expect_validation_failure root_uid_leading_zero -var='container_user=00'
expect_validation_failure malformed_vpc -var='vpc_id=vpc-1'
expect_validation_failure malformed_subnets -var='private_subnet_ids=["subnet-1","subnet-2"]'
expect_validation_failure malformed_read_secret -var='read_secret_arn=arn:aws:secretsmanager:us-east-1:123456789012:not-secret'
expect_validation_failure non_certificate_acm -var='acm_certificate_arn=arn:aws:acm:us-east-1:123456789012:certificate-authority/example'

"$PYTHON_BIN" - "$DISABLED_JSON" "$ENABLED_JSON" <<'PY'
from __future__ import annotations

import json
import sys

disabled_path, enabled_path = sys.argv[1:]
disabled = json.loads(open(disabled_path, encoding="utf-8").read())
enabled = json.loads(open(enabled_path, encoding="utf-8").read())

disabled_changes = [
    c for c in disabled.get("resource_changes", []) if c.get("change", {}).get("actions") != ["no-op"]
]
if disabled_changes:
    raise SystemExit(
        f"disabled plan must have zero resource changes; found {len(disabled_changes)}"
    )

changes = enabled.get("resource_changes", [])
types = {c.get("type") for c in changes}
after_by_type: dict[str, list[dict]] = {}
changes_by_type: dict[str, list[dict]] = {}
for change in changes:
    after = (change.get("change") or {}).get("after") or {}
    after_by_type.setdefault(change.get("type"), []).append(after)
    changes_by_type.setdefault(change.get("type"), []).append(change)

required = {
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
}
missing = sorted(required - types)
if missing:
    raise SystemExit(f"enabled plan missing resource types: {missing}")

forbidden = {
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
}
hit = sorted(types & forbidden)
if hit:
    raise SystemExit(f"enabled plan contains forbidden resource types: {hit}")

lbs = after_by_type.get("aws_lb", [])
if not lbs or not all(lb.get("internal") is True for lb in lbs):
    raise SystemExit("expected internal ALB")

listeners = after_by_type.get("aws_lb_listener", [])
if not listeners or not all(l.get("protocol") == "HTTPS" for l in listeners):
    raise SystemExit("expected HTTPS listener only")

services = after_by_type.get("aws_ecs_service", [])
if not services:
    raise SystemExit("missing ecs service")
for svc in services:
    net = svc.get("network_configuration") or []
    if isinstance(net, list):
        net_cfg = net[0] if net else {}
    else:
        net_cfg = net
    public_ip = net_cfg.get("assign_public_ip")
    if public_ip not in {"DISABLED", False, "disabled"}:
        raise SystemExit(f"assign_public_ip must be DISABLED, got {public_ip!r}")
    cb = svc.get("deployment_circuit_breaker") or {}
    if isinstance(cb, list):
        cb = cb[0] if cb else {}
    raw = json.dumps(svc)
    compact = raw.replace(" ", "").lower()
    if '"rollback":true' not in compact and cb.get("rollback") is not True:
        raise SystemExit("expected deployment circuit breaker rollback")

efs = after_by_type.get("aws_efs_file_system", [])
if not efs or not all(fs.get("encrypted") is True for fs in efs):
    raise SystemExit("expected encrypted EFS")
if not after_by_type.get("aws_efs_mount_target"):
    raise SystemExit("expected EFS mount targets")
if not after_by_type.get("aws_efs_access_point"):
    raise SystemExit("expected EFS access point")

task_defs = after_by_type.get("aws_ecs_task_definition", [])
if not task_defs:
    raise SystemExit("missing task definition")
td_raw = json.dumps(task_defs)
if "@sha256:" not in td_raw:
    raise SystemExit("expected immutable image digest in task definition")
if "valueFrom" not in td_raw and "value_from" not in td_raw:
    raise SystemExit("expected secret ARN injection via valueFrom")
if "ENTERPRISE_API_TOKEN" not in td_raw:
    raise SystemExit("expected secret-injected ENTERPRISE_API_TOKEN")
for task_def in task_defs:
    volumes = task_def.get("volume") or []
    if isinstance(volumes, dict):
        volumes = [volumes]
    efs_volumes = []
    for volume in volumes:
        config = volume.get("efs_volume_configuration") or []
        if isinstance(config, dict):
            config = [config]
        efs_volumes.extend(config)
    if not efs_volumes:
        raise SystemExit("task definition must include EFS volume configuration")
    for config in efs_volumes:
        auth = config.get("authorization_config") or []
        if isinstance(auth, dict):
            auth = [auth]
        if not auth or any(item.get("iam") != "ENABLED" for item in auth):
            raise SystemExit("EFS iam authorization must be ENABLED")

configuration_blob = json.dumps(enabled.get("configuration", {}))
for marker in (
    "elasticfilesystem:ClientMount",
    "elasticfilesystem:ClientWrite",
    "aws_efs_file_system.actions",
    "aws_efs_access_point.actions",
    "elasticfilesystem:AccessPointArn",
):
    if marker not in configuration_blob:
        raise SystemExit(f"EFS task policy configuration missing {marker}")
if "elasticfilesystem:ClientRootAccess" in configuration_blob:
    raise SystemExit("EFS task policy must not allow ClientRootAccess")

logs = after_by_type.get("aws_cloudwatch_log_group", [])
if not logs:
    raise SystemExit("missing log group")
for lg in logs:
    retention = lg.get("retention_in_days")
    if retention is None or int(retention) > 30:
        raise SystemExit(f"log retention not bounded: {retention}")

budgets = after_by_type.get("aws_budgets_budget", [])
if not budgets:
    raise SystemExit("missing budget")
budget_raw = json.dumps(budgets)
if "ACTUAL" not in budget_raw or "FORECASTED" not in budget_raw:
    raise SystemExit("budget must include ACTUAL and FORECASTED notifications")

alarms = after_by_type.get("aws_cloudwatch_metric_alarm", [])
alarm_metrics = {a.get("metric_name") for a in alarms}
expected_alarms = {"HTTPCode_ELB_5XX_Count", "TargetResponseTime", "HealthyHostCount"}
if not expected_alarms.issubset(alarm_metrics):
    raise SystemExit(f"missing alarms: {sorted(expected_alarms - alarm_metrics)}")

iam_policies = after_by_type.get("aws_iam_role_policy", [])
policy_blob = json.dumps(iam_policies)
if "secretsmanager:GetSecretValue" not in policy_blob:
    raise SystemExit("execution policy must allow GetSecretValue")
for pol in iam_policies:
    doc = pol.get("policy") or ""
    if not doc:
        continue
    parsed = json.loads(doc)
    statements = parsed.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for statement in statements:
        actions = statement.get("Action", [])
        resources = statement.get("Resource", [])
        if isinstance(actions, str):
            actions = [actions]
        if isinstance(resources, str):
            resources = [resources]
        if "secretsmanager:GetSecretValue" in actions and "*" in resources:
            raise SystemExit("secret reads must not use Resource *")
if "hermes-lab/read-fixture" not in policy_blob or "hermes-lab/write-fixture" not in policy_blob:
    raise SystemExit("secret policy must list the two provided secret ARNs")
if "ecr:GetAuthorizationToken" not in policy_blob:
    raise SystemExit("execution policy must allow ECR authorization")
if "ecr:BatchGetImage" not in policy_blob or "repository/enterprise-api" not in policy_blob:
    raise SystemExit("ECR image reads must be scoped to the provided repository ARN")

security_group_changes = changes_by_type.get("aws_security_group", [])
bounded_groups = []
for group_change in security_group_changes:
    change = group_change.get("change") or {}
    after = change.get("after") or {}
    name = str(after.get("name", ""))
    if not (name.endswith("-efs") or name.endswith("-alb")):
        continue
    bounded_groups.append(name)
    after_unknown = change.get("after_unknown") or {}
    if after_unknown.get("egress"):
        raise SystemExit(f"{name} security-group egress is unknown, not known empty")
    if after.get("egress"):
        raise SystemExit(f"{name} security group must have no inline egress rules")
if len(bounded_groups) != 2:
    raise SystemExit(f"expected known-empty ALB and EFS security groups, got {bounded_groups}")

if any(svc.get("desired_count") != 1 for svc in services):
    raise SystemExit("evaluation service must plan exactly one task")
if any(float(b.get("limit_amount")) > 25 for b in budgets):
    raise SystemExit("evaluation budget must not exceed USD 25")

print("plan_invariants_ok")
PY

# Grep source and command log to prove no apply path.
if grep -E '(^|[[:space:]])(tofu|terraform)[[:space:]]+apply\b' "$COMMAND_LOG" >/dev/null 2>&1; then
  fail "command log contains apply"
fi
if grep -E '^[[:space:]]*(tofu|terraform)[[:space:]]+apply\b' "${ROOT_DIR}/scripts/cloud-iac-proof.sh" >/dev/null; then
  fail "proof script contains apply invocation"
fi
# Infra may mention "tofu apply" / "terraform apply" only in no-apply boundary prose.
if grep -RInE 'tofu apply|terraform apply' "$IAC_SRC" >/dev/null 2>&1; then
  if grep -RInE 'tofu apply|terraform apply' "$IAC_SRC" | grep -viE 'no .*apply|without apply|not .*apply|there is no' >/dev/null 2>&1; then
    fail "infra source mentions apply outside the no-apply boundary prose"
  fi
fi
# Explicit forbidden-pattern grep (strings must appear in this script for contract tests).
grep -F "tofu apply" "${ROOT_DIR}/scripts/cloud-iac-proof.sh" >/dev/null
grep -F "terraform apply" "${ROOT_DIR}/scripts/cloud-iac-proof.sh" >/dev/null

"$PYTHON_BIN" - "$STAGED_RECEIPT" "$OPENTOFU_VERSION" "$AWS_PROVIDER_VERSION" "$ASSET_ARCH" "$ACTUAL_SHA256" "$DISABLED_JSON" "$ENABLED_JSON" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

(
    receipt_path,
    tofu_version,
    provider_version,
    arch,
    digest,
    disabled_path,
    enabled_path,
) = sys.argv[1:]

disabled = json.loads(Path(disabled_path).read_text(encoding="utf-8"))
enabled = json.loads(Path(enabled_path).read_text(encoding="utf-8"))
disabled_count = len(
    [
        c
        for c in disabled.get("resource_changes", [])
        if c.get("change", {}).get("actions") != ["no-op"]
    ]
)
managed_changes = [
    c
    for c in enabled.get("resource_changes", [])
    if c.get("mode") == "managed" and "create" in (c.get("change", {}).get("actions") or [])
]
enabled_types = sorted({c.get("type") for c in managed_changes})

after_by_type: dict[str, list[dict]] = {}
for change in managed_changes:
    after = (change.get("change") or {}).get("after") or {}
    after_by_type.setdefault(change.get("type"), []).append(after)

services = after_by_type.get("aws_ecs_service", [])
budgets = after_by_type.get("aws_budgets_budget", [])
lbs = after_by_type.get("aws_lb", [])
efs = after_by_type.get("aws_efs_file_system", [])
logs = after_by_type.get("aws_cloudwatch_log_group", [])
policies = after_by_type.get("aws_iam_role_policy", [])
task_defs = after_by_type.get("aws_ecs_task_definition", [])
security_group_changes = [
    c for c in managed_changes if c.get("type") == "aws_security_group"
]

artifact_policy_scoped = False
for policy in policies:
    document = policy.get("policy")
    if not document:
        continue
    parsed = json.loads(document)
    statements = parsed.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    secret_scoped = False
    image_scoped = False
    auth_bounded = False
    for statement in statements:
        actions = statement.get("Action", [])
        resources = statement.get("Resource", [])
        if isinstance(actions, str):
            actions = [actions]
        if isinstance(resources, str):
            resources = [resources]
        if "secretsmanager:GetSecretValue" in actions:
            secret_scoped = bool(resources) and "*" not in resources and len(resources) == 2
        if "ecr:BatchGetImage" in actions:
            image_scoped = len(resources) == 1 and resources[0] != "*"
        if "ecr:GetAuthorizationToken" in actions:
            auth_bounded = resources == ["*"]
    artifact_policy_scoped = secret_scoped and image_scoped and auth_bounded

configuration_blob = json.dumps(enabled.get("configuration", {}))
task_efs_policy_source_scoped = all(
    marker in configuration_blob
    for marker in (
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite",
        "aws_efs_file_system.actions",
        "aws_efs_access_point.actions",
        "elasticfilesystem:AccessPointArn",
    )
) and "elasticfilesystem:ClientRootAccess" not in configuration_blob

planned_efs_iam_authorization = False
for task_def in task_defs:
    volumes = task_def.get("volume") or []
    if isinstance(volumes, dict):
        volumes = [volumes]
    for volume in volumes:
        configs = volume.get("efs_volume_configuration") or []
        if isinstance(configs, dict):
            configs = [configs]
        for config in configs:
            auth = config.get("authorization_config") or []
            if isinstance(auth, dict):
                auth = [auth]
            if auth and all(item.get("iam") == "ENABLED" for item in auth):
                planned_efs_iam_authorization = True

known_empty_alb_efs_egress = True
bounded_group_count = 0
for group_change in security_group_changes:
    change = group_change.get("change") or {}
    after = change.get("after") or {}
    name = str(after.get("name", ""))
    if not (name.endswith("-alb") or name.endswith("-efs")):
        continue
    bounded_group_count += 1
    after_unknown = change.get("after_unknown") or {}
    if after_unknown.get("egress") or after.get("egress"):
        known_empty_alb_efs_egress = False
known_empty_alb_efs_egress = known_empty_alb_efs_egress and bounded_group_count == 2

planned_desired_count = services[0].get("desired_count") if services else None
planned_budget_usd = float(budgets[0].get("limit_amount")) if budgets else None
planned_internal_alb = bool(lbs and lbs[0].get("internal") is True)
network = services[0].get("network_configuration") if services else None
if isinstance(network, list):
    network = network[0] if network else None
planned_public_ip = network.get("assign_public_ip") if isinstance(network, dict) else None
planned_encrypted_efs = bool(efs and efs[0].get("encrypted") is True)
planned_log_retention_days = logs[0].get("retention_in_days") if logs else None

receipt = {
    "result": "pass",
    "deployed": False,
    "apply": False,
    "aws_refresh": False,
    "aws_account_mutations": False,
    "aws_resource_spend_created": False,
    "network_downloads": ["github.com/opentofu", "registry.opentofu.org/provider-mirror"],
    "opentofu_version": tofu_version,
    "aws_provider_version": provider_version,
    "asset_arch": arch,
    "opentofu_archive_sha256": digest,
    "disabled_plan_resource_changes": disabled_count,
    "enabled_plan_resource_changes": len(managed_changes),
    "enabled_plan_resource_types": enabled_types,
    "planned_desired_count": planned_desired_count,
    "planned_budget_usd": planned_budget_usd,
    "planned_internal_alb": planned_internal_alb,
    "planned_public_ip": planned_public_ip,
    "planned_encrypted_efs": planned_encrypted_efs,
    "planned_efs_iam_authorization": planned_efs_iam_authorization,
    "task_efs_policy_source_scoped": task_efs_policy_source_scoped,
    "known_empty_alb_efs_egress": known_empty_alb_efs_egress,
    "planned_log_retention_days": planned_log_retention_days,
    "artifact_policy_scoped": artifact_policy_scoped,
    "evidence": "offline_plan_only",
}
text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
forbidden_markers = (
    "ASIACLOUDIACTESTEXAMPLE",
    "cloudiactestdummysecret",
    "AWS_SECRET_ACCESS_KEY",
    "lab-read-token",
    "lab-write-token",
)
for marker in forbidden_markers:
    if marker in text:
        raise SystemExit(f"receipt contains forbidden marker: {marker}")
Path(receipt_path).write_text(text, encoding="utf-8")
print(receipt_path)
PY

# Publish the log first and receipt last. The receipt is the success commit marker;
# failures and timeouts leave the previous successful pair intact.
mv -f -- "$COMMAND_LOG" "$FINAL_COMMAND_LOG"
mv -f -- "$STAGED_RECEIPT" "$RECEIPT_PATH"

echo "CLOUD_IAC_PROOF_PASS opentofu=${OPENTOFU_VERSION} aws_provider=${AWS_PROVIDER_VERSION} arch=${ASSET_ARCH} deployed=false aws_refresh=false apply=false receipt=${RECEIPT_PATH}"
