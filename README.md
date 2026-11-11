# RoadGuard AI

RoadGuard AI processes road video, detects and tracks candidate road defects, estimates preliminary severity, and saves evidence for later agent decisions and human review. The implementation follows **RoadGuard AI: Architecture and Work Split**: the vision side exposes `analyze_video(path, gps_track)`, `reanalyze(frame, bbox, mode)`, and `score_severity(frame, detection)` with a stable JSON-like detection record.

## Current scope

- FastAPI service with `/health`.
- OpenCV video capture, aspect-preserving resize, image-quality signals, evidence-frame writing, and IoU tracking.
- A modular OpenCV DNN adapter for a local ONNX detector, plus injection points for tests and alternate model adapters.
- Crop-based `tiled` and `hires` reanalysis.
- Automated tests with synthetic videos/frames and an explicitly mocked detector.

**No ONNX model or dataset is bundled or downloaded.** Until a compatible, licensed model is provided and configured, real video analysis raises a clear `ModelNotConfiguredError`; the test mock is not a real detection model and its outputs must not be presented as model results.

## Requirements and setup

Python 3.11 or newer is recommended.

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Configuration is read from process environment variables; the application does not automatically parse `.env`. For a native local run, export the values into your shell (Linux/macOS: `set -a; source .env; set +a`) or set them through your IDE/process manager. PowerShell users can set needed variables with `$env:NAME="value"`. Docker can load an environment file with `--env-file .env`. Keep real credentials out of `.env` and source control. The model path is local and no secrets are required for the vision pipeline.

## Configure a pretrained ONNX model

1. Select a pretrained road-damage detector whose model card/repository explicitly permits your intended use and whose class labels include the target classes (for example `pothole` and `crack`). A general COCO model is not assumed to contain road-damage-specific classes.
2. Obtain the ONNX export from the model publisher's official release or export it from the publisher's documented pretrained weights. Verify the source, checksum when published, model card, export settings, and license before use. Do not use an unverified mirror or assume that a dataset's license also covers model weights.
3. Place the file at `models/road_damage.onnx` or set `ROADGUARD_ONNX_MODEL` to its path. The `models/` directory is intentionally not populated by this repository.
4. Set `ROADGUARD_CLASS_NAMES` to the exact class order used by the ONNX output, comma-separated. Defaults are `pothole,crack`; this is correct only if the chosen model uses that exact order.
5. Confirm the model input shape and output convention. The default adapter creates a normalized RGB blob at `640x640` and supports common YOLO-style output with `[cx,cy,w,h,class scores...]` and post-NMS rows `[x1,y1,x2,y2,confidence,class_id]`. Exports with objectness-based YOLOv5 rows, letterbox-specific coordinate conventions, segmentation outputs, or other layouts require a model-specific adapter. Do not assume compatibility from the `.onnx` extension alone.

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `ROADGUARD_ONNX_MODEL` | `models/road_damage.onnx` | Path to a locally obtained ONNX model |
| `ROADGUARD_CLASS_NAMES` | `pothole,crack` | Class names in the model's output order |
| `ROADGUARD_MODEL_INPUT_SIZE` | `640` | Square DNN input size |
| `ROADGUARD_CONFIDENCE_THRESHOLD` | `0.25` | Minimum detection confidence |
| `ROADGUARD_BLUR_THRESHOLD` | `35` | Laplacian variance below which a frame is flagged blurry |
| `ROADGUARD_DARK_THRESHOLD` | `35` | Mean grayscale brightness below which a frame is flagged dark |
| `ROADGUARD_EVIDENCE_DIR` | `artifacts/evidence` | Root directory for annotated evidence frames |

### Model and dataset licensing

No particular third-party checkpoint is selected in this foundation because the exact model source, weights license, ONNX export details, and class mapping have not been verified. Before committing or distributing model weights, review the publisher's license and any dataset terms (including redistribution, commercial use, attribution, and derivative-model restrictions); record the model URL/revision, license, checksum, class order, and export procedure in project documentation. RDD2022 is a candidate road-damage dataset mentioned in the architecture, not an included dependency or proof that a chosen checkpoint is licensed for every use. This repository contains no dataset and makes no claims about evaluation metrics.

## Vision API

```python
from app.vision import analyze_video, reanalyze, score_severity

records = analyze_video("sample.mp4", gps_track=[
    {"timestamp": 0.0, "lat": 12.9716, "lon": 77.5946},
])
```

`gps_track` can be `None`, a single `{"lat": ..., "lon": ...}` mapping, or an iterable of timestamped mappings. For each video detection the pipeline returns a record shaped like:

```json
{
  "track_id": 1,
  "frame_id": 12,
  "timestamp": 0.367,
  "gps": {"lat": 12.9716, "lon": 77.5946},
  "class": "pothole",
  "bbox": [100, 80, 220, 170],
  "confidence": 0.72,
  "severity": {"score": 0.53, "level": "medium", "area_ratio": 0.02},
  "evidence_path": "artifacts/evidence/sample/frame_000012.jpg",
  "image_quality": {
    "is_blurry": false,
    "is_dark": false,
    "laplacian_variance": 74.2,
    "mean_brightness": 82.0
  }
}
```

Values above are **illustrative schema only**, not evaluation or detection results. `bbox` uses `[x1,y1,x2,y2]` pixel coordinates in the processed frame. Frame numbering starts at 1; timestamp is `(frame_id - 1) / FPS`. GPS uses the nearest timestamped point; a single coordinate mapping is applied to all detections. Evidence images are annotated JPEGs written locally by default. An optional S3 evidence adapter exists, but live AWS behavior is not verified.

### Severity heuristic and limits

`area_ratio = bbox_area / frame_area`. `area_component = min(area_ratio / 0.10, 1)`. `score = 0.60 * confidence + 0.40 * area_component`; levels are `high` at 0.70+, `medium` at 0.40+, otherwise `low`. This is a transparent **preliminary bbox-area heuristic**, not segmented physical damage area or a validated road-safety score. Image-quality values are simple blur/brightness signals. IoU tracking is greedy, class-aware, and associates detections across adjacent processed frames; it is not a production multi-object tracker.

`reanalyze(frame, bbox, mode="tiled")` checks overlapping crop tiles. `mode="hires"` enlarges the crop before inference. It returns the highest-confidence result in original-frame coordinates, or `None`. `configure_detector(detector)` allows tests or alternate implementations to inject an object with a `predict(image)` method returning `RawDetection` objects. This injection point is not a substitute for real inference.

## Run the API and tests

```bash
uvicorn app.main:app --reload
python -m pytest -v
```

- Health: <http://127.0.0.1:8000/health>
- Interactive API docs: <http://127.0.0.1:8000/docs>

For the full container and AWS deployment preparation guide, continue to [Task 6 below](#task-6--docker-aws-deployment-preparation-and-benchmarking). Deployment instructions are templates and have not been executed against AWS. The dashboard/review endpoints have no built-in authentication and must not be exposed to an untrusted network.

## Current verification status

- **Verified locally:** FastAPI endpoints, SQLite persistence, deterministic agent rules, tracking/severity heuristics, duplicate merging and priority tests, dashboard API workflow, evidence serving, and a synthetic upload-to-review integration test using a deliberately mocked detector.
- **Implemented but not live-verified:** optional S3/DynamoDB adapters, IAM/table templates, Dockerfile, and AWS deployment guidance.
- **Blocked / unverified:** real ONNX inference (no compatible, licensed model supplied), accuracy on a labeled dataset, Docker image build (Docker was unavailable in the review environment), live AWS deployment, native Graviton comparison, and COOL runtime/benchmark.

See [the measured evaluation report](docs/evaluation-report.md), [failure cases](docs/failure-cases.md), [architecture diagrams](docs/architecture.md), [five-minute demo script](docs/demo-script.md), and [submission checklist](docs/submission-checklist.md). No AWS resources or Marketplace subscription were created, and no model accuracy or cloud deployment success is claimed.

## Deterministic agent decision engine

The `app.agent` package consumes records returned by the vision pipeline. It is rule-based and does not use an LLM.

```python
from app.agent import process_detections
from app.vision import analyze_video

records = analyze_video("sample.mp4", gps_track=None)
result = process_detections(records)
issues = result["issues"]
trace = result["trace"]
```

- **Validation:** malformed class/bbox/confidence or invalid supplied GPS is quarantined from automated prioritization and recorded in the trace.
- **Low confidence:** records below `0.45` are reanalysed through the existing vision `reanalyze(..., mode="tiled")` using `evidence_path`. Tests can inject a deterministic `reanalyzer(record)` callable. If reanalysis returns no result, raises, or returns invalid data, the original finding is retained and escalated for human review.
- **Duplicate merging:** same-class observations merge when they share a vision `track_id`, their valid GPS fixes are within 8 metres, or their same-frame bounding boxes have IoU of at least 0.50. When GPS is missing, GPS matching is skipped; same-track or same-frame spatial matching may still apply. These are configurable parameters to `process_detections` and should be tuned against real field data.
- **Escalation:** high severity (severity label `high` or score at least `0.70`) always enters `human_review_required`. Failed reanalysis also requires human review. The engine never automatically closes a road issue.
- **Priority:** a transparent 0–100 weighted score uses severity (40%), bbox-area estimate (25%), recurrence (20%), and traffic importance (15%). Traffic accepts a numeric 0–1 value or `low`/`medium`/`high`. If damage-size or traffic data is missing, available weights are renormalized and missing components are returned explicitly. This is a triage heuristic, not a validated engineering or safety score.
- **Audit trace:** every performed action emits `evidence`, `decision`, `action`, `outcome`, and `detection_id`. Missing GPS never discards an issue; the issue remains in the queue and a location follow-up action is traced. Invalid records are quarantined with a trace, and duplicates are merged with the target issue and matching rationale recorded.

The returned structure contains `issues` (each with a representative observation, all observations, recurrence count, priority and status) and `trace`. The FastAPI backend persists both issues and traces through the local SQLite repository adapter. Without a configured ONNX model, real vision inference and default low-confidence reanalysis cannot run; in that case the engine retains low-confidence findings and traces the failed reanalysis rather than pretending a model decision occurred.

## FastAPI backend and local persistence (Task 4)

The API connects the existing vision output (`analyze_video(...)`) to the deterministic agent (`process_detections(...)`) and persists issue metadata and decision traces through the `Repository` interface. The current adapter is **local SQLite only**; no AWS S3, DynamoDB, IAM, or cloud deployment is configured or claimed. Evidence JPEGs remain separate files managed by the vision pipeline; metadata stores safe relative evidence references rather than exposing absolute server paths.

### Endpoints

- `POST /analyze` — multipart upload with required `video` and optional `gps_track` form field containing either `{"lat": 12.97, "lon": 77.59}` or a JSON list of timestamped GPS points. The server generates its own upload path, runs analysis synchronously, passes returned detection records into the agent, and persists issues and traces. It returns `201` with inspection ID, detection/issue counts, and issues. If no compatible ONNX model is configured, it returns `503` rather than fabricated detections.
- `GET /locations?limit=100&status=prioritized` — returns records sorted by descending priority. `limit` is 1–500; valid statuses are `pending_decision`, `prioritized`, `human_review_required`, `approved`, and `rejected`.
- `POST /locations/{id}/review` — JSON body such as `{"decision":"approved","reviewer":"operator-1","notes":"Verified on site"}`. `decision` must be `approved` or `rejected`; extra fields are rejected. A record already reviewed cannot be reviewed a second time (`409`). Every review is appended to its trace.
- `GET /trace/{id}` — returns the trace for a location UUID, with `evidence`, `decision`, `action`, and `outcome` fields.
- `GET /health` — checks that the application can query its configured metadata store.

### Upload safety and settings

Video extensions are limited to `.mp4`, `.mov`, `.avi`, `.mkv`, and `.m4v`; content type is checked and uploads are streamed with a default 100 MiB maximum. Upload names are random server-generated values and temporary uploads are deleted after analysis. GPS coordinates are range-checked. IDs must be UUIDs, review payloads are validated, and server filesystem paths are not accepted from clients or returned in API records.

Configure local persistence using `ROADGUARD_DATABASE_PATH` (default `artifacts/roadguard.sqlite3`), `ROADGUARD_UPLOAD_DIR` (default `uploads`), and `ROADGUARD_MAX_UPLOAD_BYTES` (default `104857600`). The `Repository` abstract interface is the boundary for a future adapter; replacing SQLite with S3/DynamoDB will require a new adapter and deployment configuration, not a claim that cloud persistence already exists.

Run locally with `uvicorn app.main:app --reload` and open `/docs` for the interactive API schema. Tests use a clearly mocked vision analyzer to exercise the end-to-end API without pretending a real ONNX model is installed.


## Road-inspection review dashboard (Task 5)

Start the service with `uvicorn app.main:app --reload`, then open <http://127.0.0.1:8000/dashboard>. The plain HTML/CSS/JavaScript dashboard fetches real records from `GET /locations`, loads per-finding audit details from `GET /trace/{id}`, and submits human decisions to `POST /locations/{id}/review`. It includes search/status filters, priority-ranked cards, confidence/severity/recurrence/GPS details, evidence previews when available, loading/empty/error states, and review confirmation. No example detections are embedded in the production UI; the empty state points operators to the API docs.

Evidence is served from `GET /evidence/{reference}` only when the resolved file remains inside the configured `ROADGUARD_EVIDENCE_DIR` and has an allowed image extension (`.jpg`, `.jpeg`, `.png`, `.webp`). The endpoint does not accept absolute paths, restricts traversal and returns `X-Content-Type-Options: nosniff`. The dashboard records no credentials and relies on the existing API validation for every review action. The dashboard is intended for a trusted development environment; authentication/authorization and deployment hardening must be added before exposing review endpoints to a public network.

Dashboard and API tests: `python -m pytest -v`.

## Task 6 — Docker, AWS deployment preparation, and benchmarking

**No AWS resources have been created, launched, or changed by these instructions.** AWS deployment is opt-in. The generic Docker image is designed to build for both `linux/arm64` (Graviton) and `linux/amd64` (x86); build/test it on each target architecture before production use.

### Prerequisites

- Python 3.12 and the dependencies in `requirements.txt` for local runs; Docker Desktop/Engine with Buildx for container builds.
- A compatible, licensed ONNX road-damage model supplied separately. No real pretrained model is bundled, and the API returns `503` when the model is not configured.
- For AWS only: an AWS account, region, permissions to administer resources, an EC2 instance with an attached least-privilege instance profile, a private S3 bucket if evidence offload is enabled, a DynamoDB table if that backend is enabled, and a pre-created CloudWatch log group. Marketplace COOL access is a separate optional commercial decision.

### Build and run locally (no AWS)

```bash
cp .env.example .env
# Edit .env only if needed; keep credentials out of it.
docker build --platform linux/amd64 -t roadguard-ai:task6 .
docker run --rm --name roadguard-local -p 8000:8000 \
  -e ROADGUARD_STORAGE_BACKEND=sqlite \
  -e ROADGUARD_DATABASE_PATH=/data/roadguard.sqlite3 \
  -e ROADGUARD_UPLOAD_DIR=/data/uploads \
  -e ROADGUARD_EVIDENCE_DIR=/data/evidence \
  -v roadguard-local-data:/data roadguard-ai:task6
```

Open `http://127.0.0.1:8000/dashboard`, `http://127.0.0.1:8000/docs`, and check `http://127.0.0.1:8000/health`. The volume retains local SQLite/evidence data. For an ARM-native build on an ARM machine use `--platform linux/arm64`; Docker Desktop emulation is useful for build checks but is **not** a valid performance benchmark.

To build a multi-architecture image for a registry (registry creation/login/push are intentionally not automated):

```bash
docker buildx create --name roadguard-builder --use  # only if you do not already have a builder
docker buildx build --platform linux/amd64,linux/arm64 -t YOUR_REGISTRY/roadguard-ai:task6 --push .
```

Replace the placeholder with a registry you control. `--push` uploads the image; do not run it until you intend to publish it.

### Environment variables

| Variable | Required when | Meaning / default |
|---|---|---|
| `APP_NAME`, `APP_ENV`, `LOG_LEVEL` | Optional | Display name, environment and log level (`RoadGuard AI`, `development`, `info`) |
| `ROADGUARD_STORAGE_BACKEND` | Always | `sqlite` by default; use `dynamodb` only with an existing compatible table |
| `ROADGUARD_DATABASE_PATH` | SQLite | SQLite file path (`artifacts/roadguard.sqlite3`) |
| `ROADGUARD_UPLOAD_DIR`, `ROADGUARD_EVIDENCE_DIR` | Always | Local temporary uploads and evidence directories |
| `ROADGUARD_MAX_UPLOAD_BYTES` | Optional | Maximum upload size in bytes (default `104857600`) |
| `AWS_REGION` | AWS adapters | Region used by Boto3 |
| `ROADGUARD_S3_BUCKET`, `ROADGUARD_S3_PREFIX` | S3 evidence | Leave bucket blank to keep evidence local; prefix defaults to `evidence` |
| `ROADGUARD_DYNAMODB_TABLE` | DynamoDB backend | Existing table name matching the provided template |
| `ROADGUARD_ONNX_MODEL` | Real inference | Path to the separately supplied/licensed ONNX model |
| `ROADGUARD_CLASS_NAMES`, `ROADGUARD_MODEL_INPUT_SIZE`, `ROADGUARD_CONFIDENCE_THRESHOLD` | Model configuration | Detector labels and model preprocessing thresholds; see `.env.example` |

Do not set `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` on EC2. Use an instance profile. For local AWS testing, use the standard AWS profile/credential chain and never commit credential files.

### Optional AWS storage

The app continues to use SQLite and local evidence by default. To use DynamoDB metadata, set `ROADGUARD_STORAGE_BACKEND=dynamodb` and `ROADGUARD_DYNAMODB_TABLE` to an already-created table matching `infra/aws/dynamodb-table.yaml`. To use S3 evidence, set `ROADGUARD_S3_BUCKET` and optionally `ROADGUARD_S3_PREFIX`; leaving the bucket empty keeps local evidence. Set `AWS_REGION`. Do not provide access keys to the container: Boto3 uses the EC2 instance profile in AWS, or your existing local AWS profile for development. S3 objects are private and encrypted server-side. The optional DynamoDB table is pay-per-request, but reads/writes, indexes, backups and storage may incur charges.

S3 upload is attempted after the agent has processed detections so local evidence paths remain available during low-confidence reanalysis. If a particular evidence upload fails, the app logs a warning and preserves the local reference for that item. For a fully cloud-backed deployment, ensure evidence is written to durable storage and validate upload/error handling with the exact model and workload before relying on it.

### EC2 Graviton deployment (manual; not executed)

1. In the AWS Console, choose an Arm64 Graviton instance and supported Linux AMI, attach an instance profile with the reviewed permissions in `infra/aws/roadguard-app-policy.json`, and create/choose a CloudWatch log group `/roadguard/app`. Configure the security group to allow port 8000 only from your trusted IP or private network. Do not expose the unauthenticated dashboard/review endpoints publicly; authentication and TLS/reverse-proxy hardening are still required.
2. Install Docker using the command for your chosen AMI, then pull the multi-arch image or build the image natively. On **Amazon Linux 2023**:

```bash
sudo dnf update -y
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# Log out and reconnect before using docker without sudo.
```

On **Ubuntu 24.04**, use the official Docker Engine apt repository instructions from Docker's documentation; do not install an unreviewed convenience script on a production host. Confirm `uname -m` reports `aarch64` and `docker image inspect` shows the expected image architecture.
3. Create a deployment-only environment file on the instance, outside the repository, for example `/opt/roadguard/.env.aws`:

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
ROADGUARD_STORAGE_BACKEND=dynamodb
ROADGUARD_DYNAMODB_TABLE=YOUR_EXISTING_TABLE_NAME
ROADGUARD_DATABASE_PATH=/data/roadguard.sqlite3
ROADGUARD_UPLOAD_DIR=/data/uploads
ROADGUARD_EVIDENCE_DIR=/data/evidence
ROADGUARD_MAX_UPLOAD_BYTES=104857600
AWS_REGION=YOUR_REGION
ROADGUARD_S3_BUCKET=YOUR_PRIVATE_BUCKET
ROADGUARD_S3_PREFIX=evidence
```

Set backend and table together only if the table has been created and reviewed. For local SQLite instead, use `ROADGUARD_STORAGE_BACKEND=sqlite` and omit the DynamoDB table. Protect the file (`chmod 600 /opt/roadguard/.env.aws`) and do not put credentials in it.

4. Start the container after the log group and instance role are configured:

```bash
docker run -d --name roadguard --restart unless-stopped \
  --platform linux/arm64 -p 8000:8000 \
  --env-file /opt/roadguard/.env.aws \
  -v roadguard-data:/data \
  --log-driver=awslogs \
  --log-opt awslogs-region=YOUR_REGION \
  --log-opt awslogs-group=/roadguard/app \
  --log-opt awslogs-create-group=false \
  --log-opt awslogs-stream=roadguard-app \
  YOUR_REGISTRY/roadguard-ai:task6

docker ps
docker logs --tail 100 roadguard
curl --fail http://127.0.0.1:8000/health
```

These commands are templates, not verified deployment commands for a particular account/AMI. The app has not been deployed to EC2. The health endpoint verifies repository connectivity, not model availability or inference quality.

### Cleanup

For a local container, run `docker stop roadguard-local` if it is running, then `docker rm roadguard-local`; the `roadguard-local-data` volume remains until you explicitly remove it. To remove that local test data, run `docker volume rm roadguard-local-data` **only after confirming you no longer need it**. To stop the AWS container, run `docker stop roadguard && docker rm roadguard` on the instance; this does not terminate the EC2 instance or delete S3/DynamoDB/CloudWatch resources. Stop/terminate cloud compute, remove the instance profile/policies, and delete buckets/tables/log groups only through a deliberate account-owner cleanup after checking retained data and billing. No cloud cleanup command is run by this project.

### Reproducible OpenCV preprocessing benchmark

The script measures actual input-video decode plus resize, grayscale conversion, Gaussian blur, Canny edges, and contour extraction. It reports mean/median/p95 latency, throughput, process CPU use, OpenCV/Python versions, architecture, CPU model and input SHA-256. It deliberately does **not** claim to measure ONNX inference, the agent, API/network/storage latency, or COOL unless that exact OpenCV build is installed and verified. Do not compare results from different input hashes or different frame counts as if they were controlled experiments.

On both the x86 baseline and Graviton instance, use the same video file, dependency lock/versions, max-width, warmup, measured frame count, and benchmark command. Per-frame latency includes decoding plus the preprocessing pipeline; throughput and CPU percentages exclude warmup. Run at least 3 trials and report median trial results; note instance type, region, OS, pricing source/date, and whether the run used a native architecture (not emulation). For an initial architecture comparison, choose similarly sized instances (for example `c7i.xlarge` x86 versus `c8g.xlarge` Graviton4, only if available in the same region) and record their vCPU/memory. This is a comparison of whole instance offerings, not an architecture-only controlled experiment. Keep OpenCV versions and codec/input identical; report a separate COOL run only if the exact supported COOL build is actually active.

```bash
python -m pip install -r requirements.txt
python scripts/benchmark/benchmark_video.py /path/to/same-road-video.mp4 \
  --warmup 30 --max-frames 1000 --max-width 640 \
  --hourly-cost-usd YOUR_ON_DEMAND_HOURLY_RATE --output artifacts/benchmark-x86.json
# On Graviton, use the same video and parameters, but save to a different file:
python scripts/benchmark/benchmark_video.py /path/to/same-road-video.mp4 \
  --warmup 30 --max-frames 1000 --max-width 640 \
  --hourly-cost-usd YOUR_GRAVITON_HOURLY_RATE --output artifacts/benchmark-graviton.json
python scripts/benchmark/compare_results.py artifacts/benchmark-x86.json artifacts/benchmark-graviton.json
```

Hourly cost is an explicit user input, not an automatically fetched price. Estimated compute cost per 1,000 frames assumes continuous measured throughput and excludes idle time, storage, network, Marketplace licensing, taxes, and other services. Benchmark results are not committed as fixed values; no performance number is claimed until you run the script on real, identical input on both machines.

### COOL Marketplace investigation (optional; no subscription made)

AWS Marketplace currently lists distinct COOL offerings for Graviton3, Graviton4, and Graviton5. The Graviton4 listing describes an AMI and preconfigured Python 3.10–3.12 environments, with c8g/m8g/r8g compatibility; the Graviton5 listing describes Python 3.12–3.14 bindings but targets Graviton5. Verify the exact listing, region, trial/commercial terms, supported instance family, AMI, Python ABI, OpenCV API/version and benchmark instructions before subscribing. This repo does **not** include COOL, does not claim COOL is running, and does not alter the generic container to assume it is compatible. A valid COOL comparison should run the same input and operations with the vendor-provided optimized library on a supported native Graviton system, then compare against a clearly recorded standard OpenCV x86 baseline.

References: [COOL Graviton4 Marketplace listing](https://aws.amazon.com/marketplace/pp/prodview-fdvbfiewzuehs), [COOL Graviton5 Marketplace listing](https://aws.amazon.com/marketplace/pp/prodview-igdweaoo6prb6), [AWS Graviton container guide](https://aws.github.io/graviton/containers.html), [Boto3 credentials guide](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html), and [EC2 IAM roles](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html).
