# RoadGuard AI architecture

## Runtime architecture

```mermaid
flowchart LR
    U[Reviewer / uploader] --> API[FastAPI]
    API --> UP[Upload validation and temp file]
    UP --> VP[OpenCV video pipeline]
    VP --> D[ONNX detector adapter]
    D --> T[IoU tracking + severity + quality signals]
    T --> A[Deterministic decision agent]
    A --> M[Duplicate merge + priority + audit trace]
    M --> R[Repository interface]
    R --> SQL[(SQLite, local default)]
    R -. optional .-> DB[(DynamoDB adapter)]
    T --> E[Evidence JPEGs]
    E -. optional .-> S3[(Private S3 adapter)]
    API --> DASH[Review dashboard]
    DASH --> API
    API --> LOG[stdout/stderr logs]
```

The ONNX model and its class map must be supplied separately. The tests use an explicitly mocked detector and do not establish model accuracy. S3 and DynamoDB adapters exist but require external AWS configuration and have not been tested against a live account in this review.

## Agent workflow

```mermaid
flowchart TD
    IN[Vision detection records] --> VALID{Valid class, box, confidence, GPS?}
    VALID -- no --> Q[Quarantine from automated prioritization]
    Q --> QT[Append reason to audit trace]
    VALID -- yes --> LOW{Confidence below 0.45?}
    LOW -- yes --> RE[Try crop reanalysis]
    RE --> REOK{Valid replacement found?}
    REOK -- yes --> USE[Use reanalyzed observation]
    REOK -- no --> HUMAN[Retain finding and require human review]
    LOW -- no --> USE
    USE --> DUP{Duplicate of existing issue?}
    DUP -- yes --> MERGE[Merge observation; increment recurrence]
    DUP -- no --> NEW[Create issue]
    MERGE --> SCORE[Calculate transparent priority score]
    NEW --> SCORE
    SCORE --> HIGH{High severity or failed reanalysis?}
    HIGH -- yes --> HUMAN
    HIGH -- no --> QUEUE[Publish to review queue]
    HUMAN --> TRACE[Persist issue and decision trace]
    QUEUE --> TRACE
    TRACE --> REVIEW[Human approves or rejects]
    REVIEW --> FINAL[Persist disposition and append audit trace]
```

All decisions are deterministic rules, not LLM judgments. Priority and severity are triage heuristics and must not be treated as validated engineering or safety assessments. A human reviewer retains final disposition.
