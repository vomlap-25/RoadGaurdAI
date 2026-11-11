# RoadGuard AI evaluation report

**Review date:** 2026-10-23  
**Purpose:** submission-readiness verification of the checked-out source in this workspace.  
**Scope:** automated software tests and synthetic fixtures only; not a field or model evaluation.

## Measured results

| Check | Observed result | Interpretation |
|---|---|---|
| Automated suite | **43 passed** in **0.69 s** on the recorded run | All current tests passed in this container run; timing is environment-specific. |
| Synthetic upload-to-review workflow | Generated 5-frame MJPG video decoded; mocked detector returned 5 detections; agent merged them into 1 issue; evidence endpoint returned JPEG; approval/rejection behavior and audit traces were persisted | Verifies application integration with a mock detector, not real road-damage detection. |
| Empty/corrupt video stream | Test verifies zero decodable frames raise a clear `ValueError`; API maps it to HTTP 422 | Failure handling is covered by tests. |
| Missing model configuration | Test verifies `ModelNotConfiguredError` maps to HTTP 503 | Real ONNX inference remains unverified. |
| Cloud adapters | Mocked S3 and DynamoDB adapter tests pass as part of the suite | Does not verify IAM, live AWS access, durability, costs, or production behavior. |
| JavaScript syntax | `node --check app/review/static/dashboard.js` passed in the previous review run | Syntax check only; not a browser compatibility or accessibility audit. |
| Docker image build | Not run: Docker executable unavailable in this environment | Image build, healthcheck, architecture compatibility and container runtime remain unverified. |

The measured test count and duration above are from a single run and may vary by machine. The synthetic workflow's detector is a fixed test fixture. Its five detections and one merged issue are integration-test counts, not model predictions or accuracy metrics.

## Not measured / no claims made

- No real ONNX model file was available, so no model inference, precision, recall, F1, mAP, false-positive rate, or real-world detection latency was measured.
- No labeled evaluation dataset or field video was evaluated.
- No x86-versus-Graviton benchmark was run for this task; no speedup, throughput or cost comparison is claimed.
- No AWS resource was created or modified. S3, DynamoDB, IAM and CloudWatch were not tested against a live AWS account.
- No COOL Marketplace subscription or runtime was used.
- The severity and priority formulas are heuristics, not calibrated or independently validated safety measures.

## Reproduction

From the repository root, with dependencies installed from `requirements.txt`:

```bash
python -m pytest -q
python -m compileall -q app tests scripts
node --check app/review/static/dashboard.js
```

For a meaningful model evaluation, record the exact model source/revision/checksum/license, class mapping, preprocessing and export format; evaluate on a labeled holdout dataset; report per-class precision/recall and mAP with the evaluation protocol; and include false positives/negatives and failure slices such as night, blur, rain, camera angle, and road surface. Do not use the synthetic test suite as a substitute.
