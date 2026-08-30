# Design decisions and scope

## Feature-frozen v1

The v1 architecture is intentionally frozen. Ongoing work is limited to bug
fixes, correctness and security hardening, documentation, dependency
maintenance, and measurement. Redis, Kafka, Kubernetes, microservices, OAuth,
and similar infrastructure are not planned for this project.

## Server-authoritative results

The backend owns trajectory, collision, rack, score, lane wear, and game
lifecycle. Each mutation returns one immutable snapshot captured inside the
transaction. The frontend only displays that result. This avoids two sources of
truth and makes seeded behavior reproducible through the HTTP boundary.

## Domain code stays framework-free

The numerical model accepts dataclasses and returns dataclasses. Collision,
rack, and scorecard modules have no FastAPI or SQLAlchemy dependency. FastAPI
maps transport types at the edge; the repository adapter maps persistent rows at
the other edge.

## Memory default, SQL optional

The default Compose file uses memory storage and does not start PostgreSQL. The
SQL overlay exercises the repository boundary and verifies restart persistence
when database-backed storage is needed.

## Explicit migrations

The backend image includes Alembic, but starting the API never mutates the
schema. This keeps application startup and deployment/schema policy separate and
makes the SQL quick start honest about the migration step.

## Deterministic model

Release variance is seedable and pin collisions are deterministic. Modeling
assumptions and hand-tuned constants are named, bounded, and tested; unsupported
3D effects are listed as limitations.

## Benchmark methodology

The trajectory benchmark uses production code and stride, reports its excluded
layers, and records hardware/runtime context. It does not represent end-to-end
API capacity, and CI does not use performance thresholds from shared runners.
