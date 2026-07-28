# Security Policy

## Scope

This repository is a **fixture lab** for demonstrating Hermes Agent integration with a mock enterprise API. It is not a production deployment, does not contain real customer data, and must not be used with production credentials.

## Reporting vulnerabilities

If you discover a security issue in this lab's code or documentation, open a GitHub issue in the repository where you obtained this copy, or contact the maintainer directly. Do not disclose sensitive details in public issues if they could affect live systems outside this fixture lab.

## What not to submit

Never commit, paste, or include in issues or receipts:

- Real API keys, bearer tokens, or OAuth credentials
- Client names, production URLs, or proprietary business data
- Screenshots or exports from live Workiva, CRM, or identity systems
- Contents of your live `~/.hermes/config.yaml` or `~/.hermes/.env`

The lab uses a fixture token (`lab-read-token`) documented in `.env.example`. Treat it as non-secret test data only.

## Fixture token handling

- Set `ENTERPRISE_API_TOKEN` in your local `.env` or shell; do not embed tokens in committed YAML.
- Hermes MCP config uses `${ENTERPRISE_API_TOKEN}` interpolation resolved at connect time.
- Receipts and tool output must not echo tokens. Smoke tests assert this.

## Supported use

- Local Podman Compose on a developer machine
- Isolated `HERMES_HOME` for MCP discovery proof
- CI protocol-level smoke without live Hermes credentials

## Out of scope

This lab does not implement OIDC, Kubernetes hardening, production secret management, or runtime human-approval enforcement for mutations. All MCP tools are read/plan-only.
