from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from app.storage.cloud import DynamoDBRepository, S3EvidenceStore, configured_evidence_store


class FakeS3:
    def __init__(self):
        self.uploaded = []
        self.objects = {}

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.uploaded.append((filename, bucket, key, ExtraArgs))
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def get_object(self, Bucket, Key):
        from io import BytesIO
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}


class FakeBatchWriter:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def put_item(self, Item):
        self.table.items[(Item["PK"], Item["SK"])] = dict(Item)


class FakeClient:
    def describe_table(self, TableName):
        return {"TableName": TableName}


class FakeMeta:
    def __init__(self):
        self.client = FakeClient()


class FakeDynamoTable:
    name = "roadguard-test"

    def __init__(self):
        self.items = {}
        self.meta = FakeMeta()

    def batch_writer(self):
        return FakeBatchWriter(self)

    def get_item(self, Key, **kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

    def query(self, KeyConditionExpression=None, IndexName=None, ScanIndexForward=True, Limit=None, **kwargs):
        items = [dict(item) for item in self.items.values()]
        if IndexName == "status-priority-index":
            items = [item for item in items if item.get("item_type") == "location"]
        elif IndexName == "all-priority-index":
            items = [item for item in items if item.get("item_type") == "location"]
        else:
            # The test only needs the single known issue's partition.
            items = [item for item in items if item.get("PK", "").startswith("ISSUE#")]
        items.sort(key=lambda item: item.get("SK", ""))
        return {"Items": items[:Limit] if Limit else items}


def test_s3_evidence_upload_uses_private_key_and_encryption(tmp_path):
    evidence = tmp_path / "frame.jpg"
    evidence.write_bytes(b"test-image-bytes")
    client = FakeS3()
    store = S3EvidenceStore("private-evidence", "evidence", "us-east-1", client=client)

    key = store.upload_file(evidence, inspection_id="inspection-123")
    assert key == "evidence/inspection-123/frame.jpg"
    assert client.uploaded[0][1:] == (
        "private-evidence", key, {"ServerSideEncryption": "AES256"}
    )
    assert store.get_object(key)["Body"].read() == b"test-image-bytes"


def test_s3_evidence_store_rejects_path_traversal():
    store = S3EvidenceStore("private-evidence", client=FakeS3())
    try:
        store.get_object("../private.txt")
    except ValueError as exc:
        assert "Invalid S3 evidence key" in str(exc)
    else:
        raise AssertionError("path traversal key should be rejected")


def test_dynamodb_repository_saves_and_reads_location_and_trace():
    table = FakeDynamoTable()
    repository = DynamoDBRepository("roadguard-test", table=table)
    issue = {
        "issue_id": "issue-1", "status": "prioritized",
        "priority": {"score": 81.25}, "observations": [], "representative": {},
    }
    event = {"issue_id": "issue-1", "evidence": {}, "decision": "rank", "action": "queue", "outcome": "queued"}
    repository.save_inspection({
        "inspection_id": "inspection-1", "created_at": "2026-10-23T12:00:00+00:00",
        "filename": "road.mp4", "detection_count": 1,
    }, [issue], [event])

    assert repository.health() is True
    record = repository.get_location("issue-1")
    assert record is not None and record["priority"]["score"] == 81.25
    assert repository.list_locations(status="prioritized", limit=10)[0]["issue_id"] == "issue-1"
    trace = repository.get_trace("issue-1")
    assert trace is not None and trace[0]["action"] == "queue"
    assert json.loads(table.items[("ISSUE#issue-1", "META")]["record_json"])["issue_id"] == "issue-1"


def test_evidence_store_is_disabled_without_bucket(monkeypatch):
    monkeypatch.delenv("ROADGUARD_S3_BUCKET", raising=False)
    assert configured_evidence_store() is None
