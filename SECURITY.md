# Security

PyKnora treats tenant isolation and evidence integrity as core requirements. This document summarizes the main safeguards and known boundaries.

## Identity and sessions

- Identity is established from server-validated session/JWT context.
- Client-supplied identity fields in request bodies or query strings are not trusted for authorization.
- Sessions have limited lifetime; refresh/session handling is centralized.
- Passwords are stored using bcrypt.
- Revocation or role changes take effect on subsequent authorization checks.

## Workspace isolation

- Every request is resolved to a workspace scope.
- Workspace membership is checked before resource access.
- PostgreSQL Row-Level Security provides a second containment layer beneath application checks.
- Cross-tenant access is returned as the same `404` shape used for missing resources, avoiding existence leakage.
- Permission checks are centralized in API dependencies rather than scattered across route bodies.

## Authorization model

- Roles and permission points are data-driven.
- Read, write, delete and administrative actions are separated.
- Admin and owner-specific operations require dedicated permission points.
- The frontend uses permission metadata for UI affordances, but frontend checks are not treated as a security boundary.

## Data protection

- Real corpora, private evaluation results and credentials are not included in the public repository.
- Logs and traces should avoid storing full document bodies, secrets or personal identifiers.
- Uploads are validated by extension, content type and size before ingestion.
- Error responses use a unified envelope and do not expose raw internal exceptions.

## Prompt and evidence safety

The RAG layer is designed around these principles:

1. Only retrieved, authorized evidence can support an answer.
2. Evidence insufficiency leads to abstention, not speculation.
3. Retired document versions are not treated as current evidence.
4. Document content is data, not instructions.
5. Unauthorized sources cannot become evidence simply by being mentioned in a prompt.
6. Cross-workspace content must fail closed.

## Known boundaries

- The project is a portfolio-grade prototype, not an audited production deployment.
- It has not been hardened for multi-region deployment, compliance certification or adversarial production traffic.
- Observability and audit capabilities are intentionally scoped to the implemented application surface.
- Production deployments require secrets rotation, environment-specific configuration and infrastructure review.
