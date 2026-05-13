# SemZero OSS and Future Managed/Enterprise Boundaries

SemZero's public project is intentionally useful on its own. The open-source core is not a toy demo; it is designed for local and single-repository dbt PR assumption review.

## Open-source core

SemZero OSS includes:

- local CLI and GitHub Action scaffold
- dbt Assumption Gate for hidden SQL/model assumptions
- advisory PR comments
- typed JSON receipts with stable finding IDs
- dbt manifest blast radius
- Replay Lite from local/sample fixtures
- local feedback and exception files
- basic dashboard/reporting commands
- killer demo and dogfood examples

The OSS promise is simple: a developer should be able to try SemZero in a dbt repository, run in shadow mode, and see a real hidden-assumption review without speaking to sales.

## Natural managed/pro layers

Future managed or enterprise offerings should focus on operational pain rather than withholding the basic aha moment:

- hosted multi-repo dashboard
- centralized evidence storage
- managed GitHub/GitLab app
- managed Snowflake/Databricks history connectors
- team/owner analytics and drift memory across repositories
- SSO/SAML/SCIM and RBAC
- audit logs and compliance exports
- centralized policy rollout and exception governance
- private/VPC deployment
- onboarding, support, and SLA

## Boundary principle

OSS should remain genuinely useful for local/single-repo workflows. Paid products should make SemZero easier and safer to operate across teams, repos, evidence stores, and governance boundaries.
