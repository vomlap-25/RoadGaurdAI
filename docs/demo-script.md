# Five-minute RoadGuard AI demo

**Important:** use a short video only if a compatible, licensed ONNX model is available and configured. Otherwise, disclose that the demo uses the automated test's synthetic video and a mocked detector. Never present mock detections as real model output.

## 0:00–0:40 — Explain the problem and guardrails

“RoadGuard AI processes road video into candidate damage observations, groups repeat observations, ranks issues for review, and preserves evidence and an audit trace. The score is a heuristic, not an engineering safety assessment. A human reviewer makes the final decision.”

## 0:40–1:20 — Show the architecture

Open `docs/architecture.md`. Explain the pipeline: upload validation → video decoding and detector → tracking/severity → deterministic agent → repository and evidence → review dashboard. Point out the model is supplied separately and cloud adapters are optional.

## 1:20–2:15 — Demonstrate the API workflow

Run the service as described in `README.md`; open `/docs` and `/health`. If a real model is installed, upload a permitted sample through `POST /analyze` and show the returned inspection ID, detection count, issue count, and review status. If not, run the synthetic integration test and state clearly that the detector is mocked.

## 2:15–3:15 — Show evidence, priority, and trace

Open `/dashboard`, select a finding, and show its evidence preview, GPS if present, severity/priority components, recurrence, and decision trace. Explain that duplicate matching uses configured rules and can merge distinct physical defects or miss repeats; it needs validation on labeled field data.

## 3:15–4:10 — Review an issue

Use the dashboard's approve/reject action. Show that the status changes, the review is persisted, and a `human_review_submitted` trace event appears. Demonstrate that a second review is rejected with HTTP 409.

## 4:10–5:00 — Show verification and limitations

Run `python -m pytest -q`; report the actual current pass count from the terminal. Briefly show `docs/evaluation-report.md` and `docs/failure-cases.md`. State clearly: real-model metrics, Docker build, AWS deployment, Graviton comparison, and COOL performance are unverified unless independently run after this review.
