# Freelance Hub — Portfolio Projects

This is the git repository (origin: `kdavis-microsaas-engine`) that tracks Kelvin Davis's cloud/
DevOps portfolio projects. It contains two tracked projects; everything else in this filesystem
directory (Cloud Decoded, DecodedSix, Showing Signal, JARVIS, etc.) is a separate, independently
git-tracked repo living alongside this one on disk, not part of this repository.

---

## `01-governance-aws/` — AWS Secrets Governance Demo

A small Terraform + Lambda project demonstrating automated secrets rotation and continuous
compliance checking on AWS:

- **`terraform/secrets.tf`** — an AWS Secrets Manager secret with automatic rotation configured
  every 30 days.
- **`terraform/lambda.tf`** — the rotation Lambda (`lambda/rotator.py`, Python 3.12) implementing
  the four-step Secrets Manager rotation lifecycle (`createSecret` / `setSecret` / `testSecret` /
  `finishSecret`).
- **`terraform/config-rules.tf`** — an AWS Config recorder + rule
  (`SECRETSMANAGER_ROTATION_ENABLED_CHECK`) that continuously verifies rotation stays enabled,
  logging to a dedicated S3 bucket.
- **`terraform/github-oidc.tf`** — a GitHub Actions OIDC identity provider and IAM role
  (`role-github-actions-governance`), so CI authenticates to AWS via short-lived federated
  credentials — no long-lived AWS keys stored in GitHub.
- **State**: remote S3 backend (`tf-state-governance-402916653765-us-east-1`) with DynamoDB
  state locking.

**CI**: `.github/workflows/project1-governance-aws.yml` runs on any push/PR touching
`01-governance-aws/**` — `terraform validate` → `plan` → (on `main` push only) `apply`, then
verifies rotation is actually enabled on the deployed secret via `aws secretsmanager
describe-secret`.

To work on this project locally:

```bash
cd 01-governance-aws/terraform
terraform init
terraform plan
```

Requires AWS credentials with access to account `402916653765`, region `us-east-1`.

---

## `kdavis-microsaas-engine/` — Micro SaaS Engine

A research-validated software factory for building and launching a portfolio of micro-SaaS
products (FastAPI backend, Next.js frontend, Supabase, n8n), each targeting $4,000+ MRR before
the next product starts. This subdirectory is a full project in its own right with its own
detailed documentation — see **`kdavis-microsaas-engine/README.md`** and
**`kdavis-microsaas-engine/CLAUDE.md`** for its architecture, agent roster, and build order.

**CI**: `.github/workflows/mse-tests.yml` runs `pytest` against `kdavis-microsaas-engine/**` on
any push/PR touching that path.

---

## Other directories in this filesystem folder

Everything else you'll find in `/mnt/c/Users/Kelvin/projects/` — `kdavis-agentic-platform`
(Cloud Decoded), `decoded-six`, `showing-signal`, `jarvis-decoded`, `decoded-empire-os`,
`decoded-empire-orchestrator`, `security-landing`, `small-portfolio-hub`,
`thd-agentic-systems-website`, `kdavis-cloud-audit`, `kdavis-finops-agent`,
`kdavis-compliance-agent`, `aug27-pipeline`, and others — are their own independent git
repositories, not tracked by this repo's `.git`. Each has its own README describing what it is.

---

## Legal / access

Portfolio projects belonging to Kelvin Davis / THD Agentic Systems LLC. Not for redistribution.

---

**Built by** [Kelvin Davis](https://www.linkedin.com/in/kelvin-davis) — flagship product: [Cloud Decoded](https://theclouddecoded.com)
