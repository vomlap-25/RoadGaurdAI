# Failure cases and limitations

These examples document expected behavior. Unless stated as a measured test, they are contract examples rather than claims that every hardware, codec, or deployment variation has been exercised.

| Failure / edge case | Current behavior | Verification / limitation |
|---|---|---|
| Unsupported filename extension or content type | Upload is rejected with HTTP 415 | Automated API test. |
| Empty upload | Rejected with HTTP 422 | Implemented in upload validation. |
| Video container opens but yields no decodable frames | Analysis raises a clear error; API returns HTTP 422 and deletes the temporary upload | Automated pipeline test. |
| Video is very dark or blurry | Blur and brightness signals are attached to each emitted detection as `image_quality.is_blurry` and `image_quality.is_dark` | Threshold-based signals only. They do not currently reject the whole video, and a video with no detections has no per-detection quality record. |
| GPS omitted | Detections are retained without GPS; agent records a follow-up action and avoids GPS-based duplicate matching | Agent tests cover missing GPS; the location cannot be accurately mapped without a fix. |
| Malformed/out-of-range GPS | Upload request is rejected with HTTP 422 | Automated API tests cover out-of-range coordinates; malformed JSON and point validation are also checked in code. |
| No configured ONNX model | API returns HTTP 503 rather than fabricating detections | Automated API test; no real model inference was run. |
| Corrupt or incompatible ONNX model | OpenCV/model initialization can fail; unexpected errors are logged server-side and returned as generic HTTP 500 | No valid or invalid real ONNX model was available for end-to-end verification. Check model export convention and logs. |
| Low-confidence detection cannot be reanalysed | Finding is retained and escalated for human review; failure is traced | Agent unit tests; real crop reanalysis depends on a working model. |
| Invalid review payload or malformed issue ID | HTTP 422 for invalid decision payload, HTTP 400 for malformed UUID, HTTP 404 for unknown issue | Automated API tests. |
| A reviewed issue is submitted again | HTTP 409; the existing decision is not silently overwritten | Automated API and dashboard workflow tests. |
| Local evidence is missing or outside the configured root | Evidence endpoint returns HTTP 404 | Dashboard tests cover traversal and missing/invalid evidence paths. |
| S3/DynamoDB permissions, network, or schema mismatch | Cloud adapter operations can fail; live cloud behavior is not verified | Mock tests only. Validate role policy, table/index names, region, bucket, and error recovery before deployment. |
| Public exposure of review dashboard | Not safe as deployed without authentication/authorization and TLS/reverse-proxy hardening | Explicit deployment blocker; do not expose publicly as-is. |

The system must be treated as a decision-support prototype. Do not make automated road-safety, repair, dispatch, or funding decisions solely from its severity or priority scores.
