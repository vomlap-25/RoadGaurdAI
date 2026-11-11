"""Optional AWS adapters. AWS SDK clients are created lazily and use the standard
Boto3 credential chain (instance role in AWS; local profile/credentials locally).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from app.storage.repository import Repository


class S3EvidenceStore:
    """Private S3 evidence storage. Object keys are returned, never public URLs."""

    def __init__(self, bucket: str, prefix: str = "evidence", region: str | None = None, client=None):
        if not bucket.strip():
            raise ValueError("S3 bucket name is required")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            import boto3
            client = boto3.client("s3", region_name=region or None)
        self.client = client

    def upload_file(self, file_path: str | Path, *, inspection_id: str) -> str:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        key = "/".join(part for part in (self.prefix, inspection_id, path.name) if part)
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"})
        return key

    def get_object(self, key: str):
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise ValueError("Invalid S3 evidence key")
        return self.client.get_object(Bucket=self.bucket, Key=key)


class DynamoDBRepository(Repository):
    """Single-table DynamoDB adapter implementing the existing Repository contract.

    Required table keys: PK (string partition key), SK (string sort key).
    Required GSI `status-priority-index`: GSI1PK (string partition key),
    GSI1SK (string sort key). Create the table/index using the supplied template.
    """

    def __init__(self, table_name: str, region: str | None = None, table=None):
        if not table_name.strip():
            raise ValueError("DynamoDB table name is required")
        if table is None:
            import boto3
            table = boto3.resource("dynamodb", region_name=region or None).Table(table_name)
        self.table = table

    @staticmethod
    def _score_key(record: Mapping[str, Any]) -> str:
        score = float((record.get("priority") or {}).get("score", 0))
        created = str(record.get("created_at", ""))
        return f"{100.0 - max(0.0, min(100.0, score)):06.2f}#{created}#{record.get('issue_id', '')}"

    def health(self) -> bool:
        try:
            self.table.meta.client.describe_table(TableName=self.table.name)
            return True
        except Exception:
            return False

    def save_inspection(self, inspection: Mapping[str, Any], issues: list[dict[str, Any]], trace: list[dict[str, Any]]) -> None:
        inspection_id = str(inspection["inspection_id"])
        trace_by_issue: dict[str, list[dict[str, Any]]] = {str(i["issue_id"]): [] for i in issues}
        for event in trace:
            issue_id = event.get("issue_id")
            if issue_id is None:
                detection_id = str(event.get("detection_id", ""))
                for issue in issues:
                    if any(str(o.get("detection_id", o.get("track_id", ""))) == detection_id for o in issue.get("observations", [])):
                        issue_id = issue["issue_id"]
                        break
            if issue_id is not None and str(issue_id) in trace_by_issue:
                trace_by_issue[str(issue_id)].append(event)
        with self.table.batch_writer() as batch:
            batch.put_item(Item={
                "PK": f"INSPECTION#{inspection_id}", "SK": "META", "item_type": "inspection",
                "inspection_id": inspection_id, "created_at": str(inspection["created_at"]),
                "filename": str(inspection["filename"]), "status": str(inspection.get("status", "completed")),
                "detection_count": int(inspection.get("detection_count", 0)), "issue_count": len(issues),
                "GSI1PK": "INSPECTIONS", "GSI1SK": str(inspection["created_at"]),
            })
            for issue in issues:
                record = dict(issue)
                issue_id = str(record["issue_id"])
                status = str(record.get("status", "prioritized"))
                created_at = str(inspection["created_at"])
                batch.put_item(Item={
                    "PK": f"ISSUE#{issue_id}", "SK": "META", "item_type": "location",
                    "issue_id": issue_id, "inspection_id": inspection_id, "status": status,
                    "priority_score": Decimal(str((record.get("priority") or {}).get("score", 0))),
                    "created_at": created_at, "record_json": json.dumps(record, allow_nan=False),
                    "GSI1PK": f"STATUS#{status}", "GSI1SK": self._score_key(record),
                    "GSI2PK": "ALL_LOCATIONS", "GSI2SK": self._score_key(record),
                })
                for sequence, event in enumerate(trace_by_issue[issue_id]):
                    batch.put_item(Item={
                        "PK": f"ISSUE#{issue_id}", "SK": f"TRACE#{sequence:08d}",
                        "item_type": "trace", "trace_json": json.dumps(event, default=str, allow_nan=False),
                    })

    def list_locations(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key
        index = "status-priority-index" if status else "all-priority-index"
        partition = f"STATUS#{status}" if status else "ALL_LOCATIONS"
        response = self.table.query(
            IndexName=index, KeyConditionExpression=Key("GSI1PK" if status else "GSI2PK").eq(partition),
            Limit=limit, ScanIndexForward=True,
        )
        records = []
        for item in response.get("Items", []):
            if item.get("item_type") != "location":
                continue
            record = json.loads(item["record_json"])
            record["status"] = item["status"]
            if item.get("reviewed_at"):
                record["review"] = {"reviewed_at": item["reviewed_at"], "reviewer": item.get("reviewer"), "notes": item.get("review_notes"), "decision": item["status"]}
            records.append(record)
        return records

    def get_location(self, issue_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(Key={"PK": f"ISSUE#{issue_id}", "SK": "META"}, ConsistentRead=True)
        item = response.get("Item")
        if not item or item.get("item_type") != "location":
            return None
        record = json.loads(item["record_json"])
        record["status"] = item["status"]
        if item.get("reviewed_at"):
            record["review"] = {"reviewed_at": item["reviewed_at"], "reviewer": item.get("reviewer"), "notes": item.get("review_notes"), "decision": item["status"]}
        return record

    def get_trace(self, issue_id: str) -> list[dict[str, Any]] | None:
        from boto3.dynamodb.conditions import Key
        response = self.table.query(KeyConditionExpression=Key("PK").eq(f"ISSUE#{issue_id}"), ScanIndexForward=True)
        items = response.get("Items", [])
        if not any(item.get("SK") == "META" and item.get("item_type") == "location" for item in items):
            return None
        return [json.loads(item["trace_json"]) for item in items if item.get("item_type") == "trace"]

    def review_location(self, issue_id: str, *, decision: str, reviewer: str | None, notes: str | None) -> dict[str, Any] | None:
        from boto3.dynamodb.conditions import Key
        from botocore.exceptions import ClientError
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.table.update_item(
                Key={"PK": f"ISSUE#{issue_id}", "SK": "META"},
                UpdateExpression="SET #s = :decision, reviewed_at = :now, reviewer = :reviewer, review_notes = :notes, GSI1PK = :statuspk",
                ConditionExpression="attribute_exists(PK) AND attribute_not_exists(reviewed_at)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":decision": decision, ":now": now, ":reviewer": reviewer or "", ":notes": notes or "", ":statuspk": f"STATUS#{decision}"},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        # Keep record_json's status in sync with the indexed status.
        current = self.get_location(issue_id)
        if current is None:
            return None
        current["status"] = decision
        current["review"] = {"reviewed_at": now, "reviewer": reviewer, "notes": notes, "decision": decision}
        self.table.update_item(Key={"PK": f"ISSUE#{issue_id}", "SK": "META"}, UpdateExpression="SET record_json = :record", ExpressionAttributeValues={":record": json.dumps({k: v for k, v in current.items() if k != "review"}, allow_nan=False)})
        traces = self.get_trace(issue_id) or []
        self.table.put_item(Item={
            "PK": f"ISSUE#{issue_id}", "SK": f"TRACE#{len(traces):08d}", "item_type": "trace",
            "trace_json": json.dumps({"evidence": {"reviewer": reviewer, "notes": notes}, "decision": "human_review_submitted", "action": decision, "outcome": f"human reviewer recorded {decision}", "issue_id": issue_id, "timestamp": now}, allow_nan=False),
        })
        return self.get_location(issue_id)


def configured_evidence_store():
    bucket = os.getenv("ROADGUARD_S3_BUCKET", "").strip()
    if not bucket:
        return None
    return S3EvidenceStore(bucket, os.getenv("ROADGUARD_S3_PREFIX", "evidence"), os.getenv("AWS_REGION"))


def configured_repository() -> Repository:
    backend = os.getenv("ROADGUARD_STORAGE_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        from app.config.settings import DATABASE_PATH
        from app.storage.repository import SQLiteRepository
        return SQLiteRepository(DATABASE_PATH)
    if backend == "dynamodb":
        table = os.getenv("ROADGUARD_DYNAMODB_TABLE", "").strip()
        if not table:
            raise RuntimeError("ROADGUARD_DYNAMODB_TABLE is required when ROADGUARD_STORAGE_BACKEND=dynamodb")
        return DynamoDBRepository(table, os.getenv("AWS_REGION"))
    raise RuntimeError("ROADGUARD_STORAGE_BACKEND must be sqlite or dynamodb")
