"""Tests for the database & query API (Member D).

Split into pure-logic unit tests (no framework) and endpoint tests that run the
real FastAPI app against a fresh SQLite database via dependency overrides.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.repository import SQLiteRepository
from app.schemas import FileRecord
from app.services.query_service import (
    filter_by_min_counts,
    filter_by_species,
    to_display_keys,
)
from app.storage_client import StorageClient
from app.tag_detector import TagDetector


def _record(fid, ftype, thumb, tags, obj=None):
    return FileRecord(
        file_id=fid,
        user_id="u1",
        file_type=ftype,
        object_key=obj or f"originals/{fid}",
        thumbnail_key=thumb,
        tags=tags,
        checksum=f"sha256:{fid}",
        status="completed",
    )


# ---------------------------------------------------------------------------
# Pure query logic
# ---------------------------------------------------------------------------
class TestPureLogic:
    def test_and_logic_not_or(self):
        records = [
            _record("a", "image", None, {"dingo": 2, "wombat": 1}),
            _record("b", "image", None, {"dingo": 2}),  # missing wombat
            _record("c", "image", None, {"wombat": 1}),  # missing dingo
        ]
        out = filter_by_min_counts(records, {"dingo": 1, "wombat": 1})
        assert [r.file_id for r in out] == ["a"]

    def test_minimum_count_enforced(self):
        records = [
            _record("a", "image", None, {"koala": 3}),
            _record("b", "image", None, {"koala": 2}),
        ]
        out = filter_by_min_counts(records, {"koala": 3})
        assert [r.file_id for r in out] == ["a"]

    def test_species_requires_at_least_one(self):
        records = [_record("a", "image", None, {"dingo": 1}), _record("b", "image", None, {})]
        assert [r.file_id for r in filter_by_species(records, "dingo")] == ["a"]

    def test_display_keys_image_vs_video(self):
        records = [
            _record("img", "image", "thumbnails/img.jpg", {"dingo": 1}),
            _record("vid", "video", None, {"dingo": 1}, obj="originals/vid.mp4"),
        ]
        assert to_display_keys(records) == ["thumbnails/img.jpg", "originals/vid.mp4"]


# ---------------------------------------------------------------------------
# Endpoint tests (fresh SQLite per test)
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    repo = SQLiteRepository(str(tmp_path / "test.db"))
    # Seed with the same shape as seed.py
    repo.add(_record("f1", "image", "thumbnails/f1.jpg", {"dingo": 2, "wombat": 1}))
    repo.add(_record("f2", "image", "thumbnails/f2.jpg", {"wombat": 2, "magpie": 1}))
    repo.add(_record("f3", "video", None, {"dingo": 1, "wombat": 3}, obj="originals/v1.mp4"))

    class _Detector(TagDetector):
        def detect(self, file_name, content):
            return {"wombat": 1}

    class _Storage(StorageClient):
        def __init__(self):
            self.deleted = []

        def delete(self, keys):
            self.deleted.extend(keys)

    storage = _Storage()
    main.app.dependency_overrides[main.get_repo] = lambda: repo
    main.app.dependency_overrides[main.get_detector] = lambda: _Detector()
    main.app.dependency_overrides[main.get_storage] = lambda: storage

    with TestClient(main.app) as c:
        c.repo = repo
        c.storage = storage
        yield c

    main.app.dependency_overrides.clear()


class TestEndpoints:
    def test_by_tags_and(self, client):
        r = client.post("/query/by-tags", json={"tags": {"dingo": 1, "wombat": 1}})
        assert r.status_code == 200
        assert r.json()["count"] == 2  # f1 (image) + f3 (video)

    def test_by_tags_min_count(self, client):
        r = client.post("/query/by-tags", json={"tags": {"wombat": 2}})
        assert r.json()["count"] == 2  # f2 and f3

    def test_by_species(self, client):
        r = client.post("/query/by-species", json={"species": "magpie"})
        assert r.json()["count"] == 1

    def test_by_thumbnail_key(self, client):
        r = client.get("/query/by-thumbnail", params={"key": "thumbnails/f1.jpg"})
        assert r.status_code == 200
        assert r.json()["original_key"] == "originals/f1"

    def test_by_thumbnail_missing_404(self, client):
        r = client.get("/query/by-thumbnail", params={"key": "thumbnails/nope.jpg"})
        assert r.status_code == 404

    def test_by_file_does_not_persist(self, client):
        before = len(client.repo.all())
        r = client.post(
            "/query/by-file",
            files={"file": ("query.jpg", b"fake-image-bytes", "image/jpeg")},
        )
        assert r.status_code == 200
        assert len(client.repo.all()) == before  # query file NOT stored

    def test_tag_add(self, client):
        client.post("/tags/edit", json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 1})
        rec = next(x for x in client.repo.all() if x.file_id == "f1")
        assert rec.tags["koala"] == 1

    def test_tag_remove_ignores_missing(self, client):
        # Removing a tag that isn't present must not error and must not mutate.
        r = client.post("/tags/edit", json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 0})
        assert r.status_code == 200
        rec = next(x for x in client.repo.all() if x.file_id == "f1")
        assert "koala" not in rec.tags

    def test_delete_removes_db_and_storage(self, client):
        r = client.post("/files/delete", json={"keys": ["originals/f1"]})
        assert r.status_code == 200
        assert r.json()["deleted_db_records"] == 1
        ids = {x.file_id for x in client.repo.all()}
        assert "f1" not in ids
        assert "thumbnails/f1.jpg" in client.storage.deleted  # storage notified


# ---------------------------------------------------------------------------
# Internal metadata state machine (Member B -> Member D)
# ---------------------------------------------------------------------------
def _reserve(client, fid, checksum=None, user="u1"):
    return client.post(
        "/internal/uploads/reserve",
        json={
            "file_id": fid,
            "user_id": user,
            "checksum": checksum or f"sha256:{fid}",
            "filename": "a.jpg",
            "file_type": "image",
            "content_type": "image/jpeg",
            "size_bytes": 100,
            "object_key": f"originals/{fid}",
        },
    )


class TestMetadataEndpoints:
    def test_reserve_201_then_409(self, client):
        assert _reserve(client, "r1").status_code == 201
        assert client.repo.get("r1").status == "pending_upload"
        r2 = _reserve(client, "r1")
        assert r2.status_code == 409
        assert r2.json()["existing_file_id"] == "r1"

    def test_reserve_duplicate_checksum_other_id(self, client):
        _reserve(client, "r2", checksum="sha256:shared")
        r = _reserve(client, "r3", checksum="sha256:shared")
        assert r.status_code == 409
        assert r.json()["existing_file_id"] == "r2"

    def test_processing_lease_granted_then_denied(self, client):
        _reserve(client, "p1")
        body = {"user_id": "u1", "object_key": "originals/p1", "sequencer": "seq1"}
        assert client.post("/internal/files/p1/processing", json=body).json()["should_process"] is True
        assert client.post("/internal/files/p1/processing", json=body).json()["should_process"] is False

    def test_processing_completed_returns_false(self, client):
        _reserve(client, "p2")
        client.repo.mark_completed("p2", "originals/p2", None, "image", {}, [], "v1")
        r = client.post(
            "/internal/files/p2/processing",
            json={"user_id": "u1", "object_key": "originals/p2", "sequencer": "s"},
        )
        assert r.json()["should_process"] is False

    def test_complete_idempotent(self, client):
        _reserve(client, "c1")
        payload = {
            "user_id": "u1",
            "file_type": "image",
            "original_key": "originals/c1",
            "thumbnail_key": "thumbnails/c1.jpg",
            "tags": {"wombat": 2},
            "detections": [{"species": "wombat", "confidence": 0.9}],
            "model_version": "v1",
            "status": "completed",
        }
        assert client.put("/internal/files/c1/complete", json=payload).status_code == 200
        rec = client.repo.get("c1")
        assert rec.status == "completed" and rec.tags == {"wombat": 2}
        assert client.put("/internal/files/c1/complete", json=payload).status_code == 200

    def test_failed_truncates_and_idempotent(self, client):
        _reserve(client, "e1")
        payload = {"user_id": "u1", "error_code": "INVALID_MEDIA", "message": "x" * 500, "status": "failed"}
        assert client.put("/internal/files/e1/failed", json=payload).status_code == 200
        rec = client.repo.get("e1")
        assert rec.status == "failed" and len(rec.message) == 240
        payload2 = {"user_id": "u1", "error_code": "INVALID_MEDIA", "message": "again", "status": "failed"}
        assert client.put("/internal/files/e1/failed", json=payload2).status_code == 200

    def test_failed_does_not_downgrade_completed(self, client):
        _reserve(client, "e2")
        client.repo.mark_completed("e2", "originals/e2", None, "image", {}, [], "v1")
        client.put(
            "/internal/files/e2/failed",
            json={"user_id": "u1", "error_code": "X", "message": "m", "status": "failed"},
        )
        assert client.repo.get("e2").status == "completed"
