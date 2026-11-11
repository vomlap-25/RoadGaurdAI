# Final submission-readiness checklist

Status meanings: **Completed** = verified in this workspace; **Partial** = implemented but limited or not fully verified; **Unverified** = requires an unavailable external resource or test environment.

## Application workflow

- [x] **Completed** — Upload validation, extension/content-type checks, streamed size limit, and cleanup of temporary uploads.
- [x] **Completed** — Synthetic end-to-end upload → real OpenCV video decode → explicitly mocked detector → agent → SQLite persistence → evidence retrieval → human approval → audit trace.
- [x] **Completed** — Invalid GPS, malformed review payloads, unknown IDs, duplicate review submission, and missing-model error paths have automated coverage.
- [x] **Completed** — Zero-decodable-frame videos fail clearly rather than being recorded as a successful empty analysis.
- [x] **Completed** — Duplicate merging, priority scoring, tracking/severity heuristics, missing GPS handling, and audit traces have unit-test coverage.
- [ ] **Partial** — Poor image quality is flagged through blur/brightness heuristics; it does not currently reject the whole video or independently create a quality report when there are no detections.
- [ ] **Partial** — Dashboard and evidence/review API work in local tests; authentication, authorization, CSRF review, TLS, rate limiting, and production hardening are not included.

## Model and evaluation

- [ ] **Unverified** — Real ONNX model loading and inference: no compatible, licensed model was supplied for this review.
- [ ] **Unverified** — Accuracy on a labeled holdout dataset: no precision/recall/mAP or false-positive/negative metrics available.
- [ ] **Unverified** — Real video/codec/lighting robustness and field performance.

## Storage, containers, and deployment

- [x] **Completed** — SQLite/local evidence adapter and mocked S3/DynamoDB adapter tests.
- [ ] **Partial** — S3/DynamoDB adapters, IAM templates, DynamoDB table template, and CloudWatch logging guidance are present; live AWS integration, permissions, persistence, and costs are unverified.
- [ ] **Unverified** — Docker build and container healthcheck: Docker was unavailable in this environment.
- [ ] **Unverified** — Native Graviton and x86 image execution and performance comparison.
- [ ] **Unverified** — COOL Marketplace runtime or benchmark; no subscription or resource creation occurred.

## Submission artifacts

- [x] **Completed** — Setup/deployment instructions and pinned direct dependencies in `requirements.txt`.
- [x] **Completed** — Architecture and agent-workflow diagrams in `docs/architecture.md`.
- [x] **Completed** — Measured test report with explicit non-claims in `docs/evaluation-report.md`.
- [x] **Completed** — Failure-case documentation in `docs/failure-cases.md`.
- [x] **Completed** — Five-minute demo script in `docs/demo-script.md`.
- [x] **Completed** — This status checklist.
- [ ] **Before submission** — Add model provenance/license/checksum and real holdout metrics if a model is supplied; execute Docker build; test deployment only with account-owner approval; ensure no credentials, private data, or unlicensed weights are committed.

## Commands last run

```bash
python -m pytest -q
python -m compileall -q app tests scripts
node --check app/review/static/dashboard.js
```

Do not mark unverified items complete based solely on these local tests.
