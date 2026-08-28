"""Tests for the database & query API (Member D).

Split into pure-logic unit tests (no framework) and endpoint tests that run the
real FastAPI app against a fresh SQLite database via dependency overrides.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.notification_client import NotificationPublisher
from app.repository import SQLiteNotificationRepository, SQLiteRepository
from app.schemas import FileRecord, Notification
from app.services.notification_service import build_notifications
from app.services.query_service import (
    filter_by_min_counts,
    filter_by_species,
    to_display_keys,
)
from app.storage_client import (
    LambdaStorageClient,
    StorageClient,
    StubStorageClient,
    UnavailableStorageClient,
)
from app.tag_detector import StubTagDetector, TagDetector


INTERNAL_API_KEY = "test-internal-api-key"


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

    def test_notification_identity_is_deterministic_per_file_user_species(self):
        first = build_notifications("f1", "originals/f1", {"wombat": 1}, lambda _s: ["u2"])
        replay = build_notifications("f1", "originals/f1", {"wombat": 1}, lambda _s: ["u2"])

        assert first[0].notification_id == replay[0].notification_id


# ---------------------------------------------------------------------------
# Endpoint tests (fresh SQLite per test)
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "test.db"))
    notif_repo = SQLiteNotificationRepository(str(tmp_path / "test_notif.db"))
    # Seed with the same shape as seed.py
    repo.add(_record("f1", "image", "thumbnails/f1.jpg", {"dingo": 2, "wombat": 1}))
    repo.add(_record("f2", "image", "thumbnails/f2.jpg", {"wombat": 2, "magpie": 1}))
    repo.add(_record("f3", "video", None, {"dingo": 1, "wombat": 3}, obj="originals/v1.mp4"))
    repo.add(
        FileRecord(
            file_id="f4",
            user_id="u2",
            file_type="image",
            object_key="originals/u2/f4.jpg",
            thumbnail_key="thumbnails/u2/f4.jpg",
            tags={"dingo": 1},
            checksum="sha256:f4",
            status="completed",
        )
    )

    class _Detector(TagDetector):
        def __init__(self):
            self.calls = []

        def detect(self, **kwargs):
            self.calls.append(kwargs)
            return {"wombat": 1}

    class _Storage(StorageClient):
        def __init__(self):
            self.deleted = []
            self.deleted_by_owner = {}

        def delete(self, user_id, keys):
            self.deleted.extend(keys)
            self.deleted_by_owner.setdefault(user_id, []).extend(keys)

    class _Publisher(NotificationPublisher):
        def __init__(self):
            self.published = []

        def publish(self, notification):
            self.published.append(notification)

    storage = _Storage()
    detector = _Detector()
    publisher = _Publisher()
    main.app.dependency_overrides[main.get_repo] = lambda: repo
    main.app.dependency_overrides[main.get_notification_repo] = lambda: notif_repo
    main.app.dependency_overrides[main.get_detector] = lambda: detector
    main.app.dependency_overrides[main.get_storage] = lambda: storage
    main.app.dependency_overrides[main.get_publisher] = lambda: publisher
    main.app.dependency_overrides[main.get_current_user] = lambda: "u1"
    test_settings = SimpleNamespace(
        internal_api_key=INTERNAL_API_KEY,
        query_input_bucket="private-media",
    )
    main.app.dependency_overrides[main.get_settings] = lambda: test_settings

    with TestClient(main.app) as c:
        c.repo = repo
        c.notif_repo = notif_repo
        c.storage = storage
        c.detector = detector
        c.publisher = publisher
        yield c

    main.app.dependency_overrides.clear()


class TestEndpoints:
    def test_by_tags_preserves_archive_wide_results_across_owners(self, client):
        r = client.post("/query/by-tags", json={"tags": {"dingo": 1}})

        assert r.status_code == 200
        assert set(r.json()["results"]) == {
            "thumbnails/f1.jpg",
            "originals/v1.mp4",
            "thumbnails/u2/f4.jpg",
        }

    def test_query_results_add_safe_archive_items_and_exclude_incomplete_records(
        self, client
    ):
        client.repo.add(
            FileRecord(
                file_id="processing",
                user_id="u1",
                file_type="image",
                object_key="originals/processing.jpg",
                checksum="sha256:processing",
                tags={"dingo": 1},
                status="processing",
            )
        )

        response = client.post("/query/by-tags", json={"tags": {"dingo": 1}})

        assert response.status_code == 200
        payload = response.json()
        assert payload["results"] == [
            "thumbnails/f1.jpg",
            "originals/v1.mp4",
            "thumbnails/u2/f4.jpg",
        ]
        assert payload["count"] == 3
        assert payload["items"] == [
            {
                "file_id": "f1",
                "file_type": "image",
                "display_key": "thumbnails/f1.jpg",
                "original_key": "originals/f1",
                "thumbnail_key": "thumbnails/f1.jpg",
                "can_preview": True,
                "can_manage": True,
            },
            {
                "file_id": "f3",
                "file_type": "video",
                "display_key": "originals/v1.mp4",
                "original_key": "originals/v1.mp4",
                "thumbnail_key": None,
                "can_preview": True,
                "can_manage": True,
            },
            {
                "file_id": "f4",
                "file_type": "image",
                "display_key": "thumbnails/u2/f4.jpg",
                "original_key": "originals/u2/f4.jpg",
                "thumbnail_key": "thumbnails/u2/f4.jpg",
                "can_preview": True,
                "can_manage": False,
            },
        ]

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

    def test_by_thumbnail_url_normalizes_to_key(self, client):
        response = client.get(
            "/query/by-thumbnail",
            params={
                "key": (
                    "https://private-media.s3.ap-southeast-2.amazonaws.com/"
                    "thumbnails/f1.jpg?X-Amz-Signature=redacted"
                )
            },
        )

        assert response.status_code == 200
        assert response.json() == {"original_key": "originals/f1", "file_id": "f1"}

    def test_by_thumbnail_missing_404(self, client):
        r = client.get("/query/by-thumbnail", params={"key": "thumbnails/nope.jpg"})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "THUMBNAIL_NOT_FOUND"

    def test_by_thumbnail_rejects_untrusted_media_url(self, client):
        response = client.get(
            "/query/by-thumbnail",
            params={"key": "https://wrong-bucket.s3.amazonaws.com/thumbnails/f1.jpg"},
        )

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_MEDIA_REFERENCE"

    def test_by_file_does_not_persist(self, client):
        before = len(client.repo.all())
        r = client.post(
            "/query/by-file",
            files={"file": ("query.jpg", b"fake-image-bytes", "image/jpeg")},
        )
        assert r.status_code == 200
        assert len(client.repo.all()) == before  # query file NOT stored
        assert client.detector.calls == [
            {
                "user_id": "u1",
                "file_name": "query.jpg",
                "content_type": "image/jpeg",
                "content": b"fake-image-bytes",
            }
        ]

    def test_by_file_with_no_detected_tags_returns_no_archive_results(self, client):
        class _EmptyDetector(TagDetector):
            def detect(self, **_kwargs):
                return {}

        main.app.dependency_overrides[main.get_detector] = lambda: _EmptyDetector()

        response = client.post(
            "/query/by-file",
            files={"file": ("query.jpg", b"image", "image/jpeg")},
        )

        assert response.status_code == 200
        assert response.json() == {"results": [], "count": 0, "items": []}

    @pytest.mark.parametrize(
        "content_type", ["image/jpeg", "image/png", "image/webp"]
    )
    def test_by_file_accepts_supported_image_types(self, client, content_type):
        response = client.post(
            "/query/by-file",
            files={"file": ("query", b"image", content_type)},
        )

        assert response.status_code == 200

    def test_by_file_rejects_non_image_before_detection(self, client):
        response = client.post(
            "/query/by-file",
            files={"file": ("query.txt", b"not-an-image", "text/plain")},
        )

        assert response.status_code == 415
        assert client.detector.calls == []

    def test_by_file_rejects_image_over_front_door_limit_before_detection(self, client):
        response = client.post(
            "/query/by-file",
            files={"file": ("query.jpg", b"x" * 4_194_305, "image/jpeg")},
        )

        assert response.status_code == 413
        assert client.detector.calls == []

    def test_by_file_accepts_image_at_exact_front_door_limit(self, client):
        response = client.post(
            "/query/by-file",
            files={"file": ("query.jpg", b"x" * 4_194_304, "image/jpeg")},
        )

        assert response.status_code == 200
        assert len(client.detector.calls) == 1
        assert len(client.detector.calls[0]["content"]) == 4_194_304

    def test_by_file_returns_503_when_remote_detector_config_is_missing(self, client):
        missing_remote = main._build_detector(
            SimpleNamespace(
                tag_detector_backend="remote",
                query_input_bucket="",
                inference_api_url="",
                internal_api_key="",
            )
        )
        main.app.dependency_overrides[main.get_detector] = lambda: missing_remote

        response = client.post(
            "/query/by-file",
            files={"file": ("query.jpg", b"image", "image/jpeg")},
        )

        assert response.status_code == 503

    def test_adapter_builders_require_explicit_backends_and_production_config(
        self, monkeypatch
    ):
        assert isinstance(
            main._build_storage(
                SimpleNamespace(
                    storage_backend="stub", storage_delete_function_name=""
                )
            ),
            StubStorageClient,
        )
        assert isinstance(
            main._build_storage(
                SimpleNamespace(storage_backend="", storage_delete_function_name="")
            ),
            UnavailableStorageClient,
        )
        assert isinstance(
            main._build_detector(
                SimpleNamespace(
                    tag_detector_backend="stub",
                    query_input_bucket="",
                    inference_api_url="",
                    internal_api_key="",
                )
            ),
            StubTagDetector,
        )

        sentinel_storage = object()
        monkeypatch.setattr(
            main,
            "LambdaStorageClient",
            lambda function_name: sentinel_storage
            if function_name == "storage-delete"
            else None,
        )
        assert (
            main._build_storage(
                SimpleNamespace(
                    storage_backend="lambda",
                    storage_delete_function_name="storage-delete",
                )
            )
            is sentinel_storage
        )
        with pytest.raises(RuntimeError, match="function name"):
            main._build_storage(
                SimpleNamespace(
                    storage_backend="lambda", storage_delete_function_name=""
                )
            )
        with pytest.raises(RuntimeError, match="STORAGE_BACKEND"):
            main._build_storage(
                SimpleNamespace(
                    storage_backend="unexpected", storage_delete_function_name=""
                )
            )

        sentinel_detector = object()
        monkeypatch.setattr(main, "RemoteTagDetector", lambda **kwargs: sentinel_detector)
        assert (
            main._build_detector(
                SimpleNamespace(
                    tag_detector_backend="remote",
                    query_input_bucket="private-media",
                    inference_api_url="https://inference.example",
                    internal_api_key="internal-key",
                )
            )
            is sentinel_detector
        )

    def test_tag_add(self, client):
        client.post("/tags/edit", json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 1})
        rec = next(x for x in client.repo.all() if x.file_id == "f1")
        assert rec.tags["koala"] == 1

    def test_tag_edit_deduplicates_the_same_legacy_key_and_url(self, client):
        original_by_keys = client.repo.by_keys
        lookup_keys = []

        def observe_by_keys(keys):
            lookup_keys.append(keys)
            return original_by_keys(keys)

        client.repo.by_keys = observe_by_keys
        response = client.post(
            "/tags/edit",
            json={
                "keys": ["originals/f1"],
                "urls": [
                    "https://private-media.s3.ap-southeast-2.amazonaws.com/"
                    "originals/f1?X-Amz-Signature=redacted",
                ],
                "tags": ["koala"],
                "operation": 1,
            },
        )

        assert response.status_code == 200
        assert response.json() == {"updated": 1, "matched_keys": ["originals/f1"]}
        assert lookup_keys == [["originals/f1"]]
        assert client.repo.get("f1").tags["koala"] == 1

    def test_tag_edit_accepts_url_only_references(self, client):
        response = client.post(
            "/tags/edit",
            json={
                "urls": [
                    "https://private-media.s3.ap-southeast-2.amazonaws.com/"
                    "originals/f1?X-Amz-Signature=redacted"
                ],
                "tags": ["koala"],
                "operation": 1,
            },
        )

        assert response.status_code == 200
        assert response.json() == {"updated": 1, "matched_keys": ["originals/f1"]}

    def test_tag_edit_normalizes_scientific_names_before_persistence(self, client):
        response = client.post(
            "/tags/edit",
            json={
                "keys": ["originals/f1"],
                "tags": ["Vombatus_ursinus"],
                "operation": 1,
            },
        )

        assert response.status_code == 200
        record = client.repo.get("f1")
        assert record.tags["wombat"] == 2
        assert "Vombatus_ursinus" not in record.tags

    def test_tag_remove_ignores_missing(self, client):
        # Removing a tag that isn't present must not error and must not mutate.
        r = client.post("/tags/edit", json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 0})
        assert r.status_code == 200
        rec = next(x for x in client.repo.all() if x.file_id == "f1")
        assert "koala" not in rec.tags

    @pytest.mark.parametrize(
        "keys",
        [
            ["originals/u2/f4.jpg"],
            ["originals/f1", "originals/u2/f4.jpg"],
        ],
        ids=["foreign", "mixed"],
    )
    def test_tag_edit_rejects_entire_request_when_any_record_is_foreign(
        self, client, keys
    ):
        before = {record.file_id: record.tags for record in client.repo.all()}

        r = client.post(
            "/tags/edit", json={"keys": keys, "tags": ["koala"], "operation": 1}
        )

        assert r.status_code == 403
        assert r.json()["detail"] == {
            "code": "FORBIDDEN_OWNER",
            "message": "media is not owned by the authenticated user",
        }
        assert {record.file_id: record.tags for record in client.repo.all()} == before

    def test_delete_removes_db_and_storage(self, client):
        r = client.post("/files/delete", json={"keys": ["originals/f1"]})
        assert r.status_code == 200
        assert r.json()["deleted_db_records"] == 1
        ids = {x.file_id for x in client.repo.all()}
        assert "f1" not in ids
        assert "thumbnails/f1.jpg" in client.storage.deleted  # storage notified
        # Storage delete is grouped by owner so Member B's guarded delete can
        # enforce per-user key-prefix ownership.
        assert client.storage.deleted_by_owner == {
            "u1": ["originals/f1", "thumbnails/f1.jpg"]
        }

    def test_delete_accepts_thumbnail_url_and_preserves_unknown_reference_noop(self, client):
        response = client.post(
            "/files/delete",
            json={
                "keys": ["originals/unknown"],
                "urls": [
                    "https://private-media.s3.ap-southeast-2.amazonaws.com/"
                    "thumbnails/f1.jpg?X-Amz-Signature=redacted"
                ],
            },
        )

        assert response.status_code == 200
        assert response.json()["deleted_db_records"] == 1
        assert client.repo.get("f1") is None

    def test_delete_accepts_url_only_references(self, client):
        response = client.post(
            "/files/delete",
            json={
                "urls": [
                    "https://private-media.s3.ap-southeast-2.amazonaws.com/"
                    "thumbnails/f1.jpg?X-Amz-Signature=redacted"
                ],
            },
        )

        assert response.status_code == 200
        assert response.json()["deleted_db_records"] == 1
        assert client.repo.get("f1") is None

    @pytest.mark.parametrize("route", ["/tags/edit", "/files/delete"])
    def test_management_routes_reject_empty_media_references(self, client, route):
        payload = {"keys": [], "urls": []}
        if route == "/tags/edit":
            payload.update({"tags": ["koala"], "operation": 1})

        response = client.post(route, json=payload)

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_MEDIA_REFERENCE"

    def test_delete_keeps_metadata_when_storage_deletion_fails(self, client):
        def fail_delete(_user_id, _keys):
            raise RuntimeError("storage unavailable")

        client.storage.delete = fail_delete

        with pytest.raises(RuntimeError, match="storage unavailable"):
            client.post("/files/delete", json={"keys": ["originals/f1"]})

        assert client.repo.get("f1") is not None

    def test_delete_returns_503_and_keeps_metadata_when_storage_config_is_missing(
        self, client
    ):
        unavailable = main._build_storage(
            SimpleNamespace(storage_backend="", storage_delete_function_name="")
        )
        main.app.dependency_overrides[main.get_storage] = lambda: unavailable

        response = client.post("/files/delete", json={"keys": ["originals/f1"]})

        assert response.status_code == 503
        assert client.repo.get("f1") is not None

    def test_delete_keeps_metadata_when_lambda_adapter_rejects_nested_failure(
        self, client
    ):
        class _Payload:
            def read(self, limit=-1):
                return json.dumps(
                    {
                        "statusCode": 500,
                        "body": json.dumps({"code": "INTERNAL_ERROR"}),
                    }
                ).encode()[:limit]

        class _Lambda:
            def invoke(self, **_kwargs):
                return {"StatusCode": 200, "Payload": _Payload()}

        main.app.dependency_overrides[main.get_storage] = lambda: LambdaStorageClient(
            "storage-delete", lambda_client=_Lambda()
        )

        response = client.post("/files/delete", json={"keys": ["originals/f1"]})

        assert response.status_code == 502
        assert response.json() == {"detail": "storage deletion failed"}
        assert client.repo.get("f1") is not None

    @pytest.mark.parametrize(
        "keys",
        [
            ["originals/u2/f4.jpg"],
            ["originals/f1", "originals/u2/f4.jpg"],
        ],
        ids=["foreign", "mixed"],
    )
    def test_delete_rejects_entire_request_when_any_record_is_foreign(
        self, client, keys
    ):
        before = {record.file_id for record in client.repo.all()}

        r = client.post("/files/delete", json={"keys": keys})

        assert r.status_code == 403
        assert r.json()["detail"] == {
            "code": "FORBIDDEN_OWNER",
            "message": "media is not owned by the authenticated user",
        }
        assert {record.file_id for record in client.repo.all()} == before
        assert client.storage.deleted == []


# ---------------------------------------------------------------------------
# Internal metadata state machine (Member B -> Member D)
# ---------------------------------------------------------------------------
def _reserve(client, fid, checksum=None, user="u1"):
    return client.post(
        "/internal/uploads/reserve",
        headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
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
    def test_pending_reservation_is_reused_with_original_upload_identity(self, client):
        first = _reserve(client, "r1", checksum="sha256:shared")
        second = _reserve(client, "r-new", checksum="sha256:shared")

        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json() == {
            "file_id": "r1",
            "object_key": "originals/r1",
            "status": "pending_upload",
            "reused": True,
        }
        assert client.repo.get("r-new") is None

    def test_completed_reservation_remains_duplicate(self, client):
        _reserve(client, "r2", checksum="sha256:shared")
        client.repo.mark_completed("r2", "originals/r2", None, "image", {}, [], "v1")
        r = _reserve(client, "r3", checksum="sha256:shared")
        assert r.status_code == 409
        assert r.json()["existing_file_id"] == "r2"

    def test_failed_reservation_is_reset_and_reused(self, client):
        _reserve(client, "failed-old", checksum="sha256:retry")
        client.repo.mark_failed("failed-old", "INVALID_MEDIA", "temporary")

        response = _reserve(client, "failed-new", checksum="sha256:retry")

        assert response.status_code == 201
        assert response.json()["file_id"] == "failed-old"
        assert response.json()["object_key"] == "originals/failed-old"
        assert response.json()["reused"] is True
        record = client.repo.get("failed-old")
        assert record.status == "pending_upload"
        assert record.error_code is None
        assert record.message is None

    def test_reuse_rejects_changed_immutable_upload_metadata(self, client):
        _reserve(client, "metadata-old", checksum="sha256:metadata")

        response = client.post(
            "/internal/uploads/reserve",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={
                "file_id": "metadata-new",
                "user_id": "u1",
                "checksum": "sha256:metadata",
                "filename": "changed.jpg",
                "file_type": "image",
                "content_type": "image/jpeg",
                "size_bytes": 100,
                "object_key": "originals/metadata-new",
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "METADATA_CONFLICT"

    def test_delete_releases_checksum_for_a_new_upload(self, client):
        _reserve(client, "delete-old", checksum="sha256:delete")
        delete_response = client.post(
            "/files/delete", json={"keys": ["originals/delete-old"]}
        )

        replacement = _reserve(client, "delete-new", checksum="sha256:delete")

        assert delete_response.status_code == 200
        assert replacement.status_code == 201
        assert replacement.json()["file_id"] == "delete-new"

    def test_processing_lease_granted_then_denied(self, client):
        _reserve(client, "p1")
        body = {"user_id": "u1", "object_key": "originals/p1", "sequencer": "seq1"}
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
        acquired = client.post(
            "/internal/files/p1/processing", json=body, headers=headers
        ).json()
        active = client.post(
            "/internal/files/p1/processing", json=body, headers=headers
        ).json()
        assert acquired == {"should_process": True, "state": "acquired"}
        assert active == {"should_process": False, "state": "lease_active"}

    def test_processing_completed_returns_false(self, client):
        _reserve(client, "p2")
        client.repo.mark_completed("p2", "originals/p2", None, "image", {}, [], "v1")
        r = client.post(
            "/internal/files/p2/processing",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={"user_id": "u1", "object_key": "originals/p2", "sequencer": "s"},
        )
        assert r.json() == {"should_process": False, "state": "completed"}

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
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
        assert client.put("/internal/files/c1/complete", json=payload, headers=headers).status_code == 200
        rec = client.repo.get("c1")
        assert rec.status == "completed" and rec.tags == {"wombat": 2}
        assert client.put("/internal/files/c1/complete", json=payload, headers=headers).status_code == 200

    def test_complete_normalizes_tags_and_detection_species_before_writing(self, client):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "normalize")

        response = client.put(
            "/internal/files/normalize/complete",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={
                "user_id": "u1",
                "file_type": "image",
                "original_key": "originals/normalize",
                "tags": {"Vombatus_ursinus": 2},
                "detections": [
                    {"species": "Vombatus_ursinus", "confidence": 0.94}
                ],
                "model_version": "v1",
            },
        )

        assert response.status_code == 200
        record = client.repo.get("normalize")
        assert record.tags == {"wombat": 2}
        assert record.detections == [{"species": "wombat", "confidence": 0.94}]
        assert [n.species for n in client.notif_repo.notifications("u2")] == ["wombat"]

    def test_failed_truncates_and_idempotent(self, client):
        _reserve(client, "e1")
        payload = {"user_id": "u1", "error_code": "INVALID_MEDIA", "message": "x" * 500, "status": "failed"}
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
        assert client.put("/internal/files/e1/failed", json=payload, headers=headers).status_code == 200
        rec = client.repo.get("e1")
        assert rec.status == "failed" and len(rec.message) == 240
        payload2 = {"user_id": "u1", "error_code": "INVALID_MEDIA", "message": "again", "status": "failed"}
        assert client.put("/internal/files/e1/failed", json=payload2, headers=headers).status_code == 200

    def test_failed_does_not_downgrade_completed(self, client):
        _reserve(client, "e2")
        client.repo.mark_completed("e2", "originals/e2", None, "image", {}, [], "v1")
        client.put(
            "/internal/files/e2/failed",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={"user_id": "u1", "error_code": "X", "message": "m", "status": "failed"},
        )
        assert client.repo.get("e2").status == "completed"

    def test_sqlite_mark_failed_condition_blocks_stale_callback_downgrade(self, client):
        _reserve(client, "e2-race")
        client.repo.mark_completed(
            "e2-race", "originals/e2-race", None, "image", {}, [], "v1"
        )

        client.repo.mark_failed("e2-race", "INFERENCE_FAILED", "late callback")

        assert client.repo.get("e2-race").status == "completed"

    def test_sqlite_first_complete_wins_concurrent_callback_race(self, client):
        _reserve(client, "complete-race")
        client.repo.mark_completed(
            "complete-race",
            "originals/complete-race",
            None,
            "image",
            {"wombat": 1},
            [],
            "first",
        )

        client.repo.mark_completed(
            "complete-race",
            "originals/complete-race",
            None,
            "image",
            {"fox": 9},
            [],
            "late",
        )

        record = client.repo.get("complete-race")
        assert record.tags == {"wombat": 1}
        assert record.model_version == "first"

    @pytest.mark.parametrize("route", ["reserve", "processing", "complete", "failed"])
    @pytest.mark.parametrize(
        ("configured_key", "request_key", "expected_status", "expected_code"),
        [
            ("", INTERNAL_API_KEY, 503, "INTERNAL_AUTH_NOT_CONFIGURED"),
            (INTERNAL_API_KEY, None, 401, "INVALID_INTERNAL_API_KEY"),
            (INTERNAL_API_KEY, "wrong-key", 401, "INVALID_INTERNAL_API_KEY"),
            (INTERNAL_API_KEY, INTERNAL_API_KEY, 200, None),
        ],
        ids=["server-secret-absent", "header-absent", "header-wrong", "header-correct"],
    )
    def test_internal_routes_fail_closed(
        self, client, route, configured_key, request_key, expected_status, expected_code
    ):
        main.app.dependency_overrides[main.get_settings] = lambda: SimpleNamespace(
            internal_api_key=configured_key
        )
        headers = (
            {"X-Internal-Api-Key": request_key} if request_key is not None else {}
        )
        if route == "reserve":
            response = client.post(
                "/internal/uploads/reserve",
                headers=headers,
                json={
                    "file_id": "auth-reserve",
                    "user_id": "u1",
                    "checksum": "sha256:auth-reserve",
                    "filename": "a.jpg",
                    "file_type": "image",
                    "content_type": "image/jpeg",
                    "size_bytes": 100,
                    "object_key": "originals/auth-reserve",
                },
            )
            success_status = 201
        else:
            client.repo.add(
                FileRecord(
                    file_id=f"auth-{route}",
                    user_id="u1",
                    file_type="image",
                    object_key=f"originals/auth-{route}",
                    checksum=f"sha256:auth-{route}",
                    status="pending_upload",
                )
            )
            if route == "processing":
                response = client.post(
                    "/internal/files/auth-processing/processing",
                    headers=headers,
                    json={
                        "user_id": "u1",
                        "object_key": "originals/auth-processing",
                        "sequencer": "seq",
                    },
                )
            elif route == "complete":
                response = client.put(
                    "/internal/files/auth-complete/complete",
                    headers=headers,
                    json={
                        "user_id": "u1",
                        "file_type": "image",
                        "original_key": "originals/auth-complete",
                        "thumbnail_key": None,
                        "tags": {},
                        "detections": [],
                        "model_version": "v1",
                    },
                )
            else:
                response = client.put(
                    "/internal/files/auth-failed/failed",
                    headers=headers,
                    json={"user_id": "u1", "error_code": "INVALID_MEDIA"},
                )
            success_status = 200

        assert response.status_code == (
            success_status if expected_status == 200 else expected_status
        )
        if expected_code is not None:
            detail = response.json()["detail"]
            assert isinstance(detail, dict)
            assert detail["code"] == expected_code
            assert isinstance(detail["message"], str) and detail["message"]

    @pytest.mark.parametrize(
        ("route", "payload"),
        [
            (
                "processing",
                {"user_id": "u2", "object_key": "originals/conflict", "sequencer": "s"},
            ),
            (
                "processing",
                {"user_id": "u1", "object_key": "originals/wrong", "sequencer": "s"},
            ),
            (
                "complete",
                {
                    "user_id": "u2",
                    "file_type": "image",
                    "original_key": "originals/conflict",
                    "tags": {},
                    "detections": [],
                },
            ),
            (
                "complete",
                {
                    "user_id": "u1",
                    "file_type": "image",
                    "original_key": "originals/wrong",
                    "tags": {},
                    "detections": [],
                },
            ),
            (
                "complete",
                {
                    "user_id": "u1",
                    "file_type": "video",
                    "original_key": "originals/conflict",
                    "tags": {},
                    "detections": [],
                },
            ),
            ("failed", {"user_id": "u2", "error_code": "INVALID_MEDIA"}),
        ],
        ids=[
            "processing-user",
            "processing-object-key",
            "complete-user",
            "complete-object-key",
            "complete-file-type",
            "failed-user",
        ],
    )
    def test_internal_transition_rejects_reserved_metadata_conflicts(
        self, client, route, payload
    ):
        client.repo.add(
            FileRecord(
                file_id="conflict",
                user_id="u1",
                file_type="image",
                object_key="originals/conflict",
                checksum="sha256:conflict",
                status="pending_upload",
            )
        )
        before = client.repo.get("conflict")

        response = getattr(client, "post" if route == "processing" else "put")(
            f"/internal/files/conflict/{route}",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json=payload,
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "METADATA_CONFLICT"
        assert client.repo.get("conflict") == before


# ---------------------------------------------------------------------------
# Subscriptions & notification trigger (Member E frontend -> Member D)
# ---------------------------------------------------------------------------
class TestSubscriptionAndNotification:
    def test_subscribe_and_list(self, client):
        r = client.post("/notifications/subscribe", json={"species": "wombat"})
        assert r.status_code == 201
        assert r.json()["user_id"] == "u1"
        r = client.get("/notifications/subscriptions")
        assert r.json() == {"species": ["wombat"], "count": 1}

    def test_subscribe_rejects_public_user_id(self, client):
        r = client.post(
            "/notifications/subscribe", json={"user_id": "u2", "species": "wombat"}
        )

        assert r.status_code == 422
        assert client.notif_repo.subscriptions("u1") == []
        assert client.notif_repo.subscriptions("u2") == []

    def test_subscription_species_is_normalized_before_persistence(self, client):
        response = client.post(
            "/notifications/subscribe", json={"species": "Vombatus_ursinus"}
        )

        assert response.status_code == 201
        assert response.json()["species"] == "wombat"
        assert client.notif_repo.subscriptions("u1") == ["wombat"]

        response = client.delete(
            "/notifications/subscribe", params={"species": "Vombatus_ursinus"}
        )
        assert response.status_code == 200
        assert client.notif_repo.subscriptions("u1") == []

    def test_unsubscribe_idempotent(self, client):
        client.post("/notifications/subscribe", json={"species": "wombat"})
        client.delete("/notifications/subscribe", params={"species": "wombat"})
        assert client.get("/notifications/subscriptions").json()["count"] == 0
        # second unsubscribe is a no-op
        r = client.delete("/notifications/subscribe", params={"species": "wombat"})
        assert r.status_code == 200

    def test_subscription_list_ignores_caller_supplied_user_id(self, client):
        client.notif_repo.subscribe("u1", "wombat")
        client.notif_repo.subscribe("u2", "magpie")

        response = client.get(
            "/notifications/subscriptions", params={"user_id": "u2"}
        )

        assert response.json() == {"species": ["wombat"], "count": 1}

    def test_notification_list_ignores_caller_supplied_user_id(self, client):
        client.notif_repo.add_notification(
            Notification(
                notification_id="mine",
                user_id="u1",
                file_id="f1",
                species="wombat",
                object_key="originals/f1",
            )
        )
        client.notif_repo.add_notification(
            Notification(
                notification_id="foreign",
                user_id="u2",
                file_id="f4",
                species="dingo",
                object_key="originals/u2/f4.jpg",
            )
        )

        response = client.get("/notifications", params={"user_id": "u2"})

        assert [item["notification_id"] for item in response.json()["notifications"]] == [
            "mine"
        ]

    def _complete(self, client, file_id, tags):
        return client.put(
            f"/internal/files/{file_id}/complete",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={
                "user_id": "u1",
                "file_type": "image",
                "original_key": f"originals/{file_id}",
                "thumbnail_key": None,
                "tags": tags,
                "detections": [],
                "model_version": "v1",
                "status": "completed",
            },
        )

    def test_complete_triggers_notification(self, client):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "n1")
        assert self._complete(client, "n1", {"wombat": 2}).status_code == 200
        notifs = client.notif_repo.notifications("u2")
        assert len(notifs) == 1
        assert notifs[0].species == "wombat"
        assert notifs[0].file_id == "n1"
        assert notifs[0].object_key == "originals/n1"
        assert len(client.publisher.published) == 1

    def test_complete_no_match_no_notification(self, client):
        client.notif_repo.subscribe("u2", "magpie")
        _reserve(client, "n2")
        self._complete(client, "n2", {"wombat": 1})
        assert client.notif_repo.notifications("u2") == []

    def test_complete_replay_no_duplicate_notifications(self, client):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "n3")
        self._complete(client, "n3", {"wombat": 1})
        self._complete(client, "n3", {"wombat": 1})  # idempotent replay
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.publisher.published) == 1

    def test_publish_failure_keeps_pending_inbox_and_completed_replay_retries(
        self, client
    ):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "n-retry")
        attempts = []

        def fail_after_observing_pending(notification):
            attempts.append(notification.notification_id)
            assert [
                item.notification_id
                for item in client.notif_repo.pending_for_file("n-retry")
            ] == [notification.notification_id]
            raise RuntimeError("temporary SNS outage")

        client.publisher.publish = fail_after_observing_pending

        first = self._complete(client, "n-retry", {"wombat": 1})

        assert first.status_code == 200
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.notif_repo.pending_for_file("n-retry")) == 1

        client.publisher.publish = lambda notification: attempts.append(
            notification.notification_id
        )
        replay = self._complete(client, "n-retry", {"wombat": 1})
        delivered_replay = self._complete(client, "n-retry", {"wombat": 1})

        assert replay.status_code == 200
        assert delivered_replay.status_code == 200
        assert attempts[0] == attempts[1]
        assert len(attempts) == 2
        assert client.notif_repo.pending_for_file("n-retry") == []

    def test_inbox_is_ensured_before_mark_completed_and_retry_is_idempotent(
        self, client
    ):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "n-mark-fails")
        original_mark_completed = client.repo.mark_completed
        client.repo.mark_completed = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("DynamoDB update failed")
        )

        with pytest.raises(RuntimeError, match="DynamoDB update failed"):
            self._complete(client, "n-mark-fails", {"wombat": 1})

        assert client.repo.get("n-mark-fails").status == "pending_upload"
        assert len(client.notif_repo.pending_for_file("n-mark-fails")) == 1

        client.repo.mark_completed = original_mark_completed
        replay = self._complete(client, "n-mark-fails", {"wombat": 1})

        assert replay.status_code == 200
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.publisher.published) == 1

    def test_completed_replay_does_not_notify_late_subscribers(self, client):
        _reserve(client, "n-late-subscriber")
        self._complete(client, "n-late-subscriber", {"wombat": 1})
        client.notif_repo.subscribe("u2", "wombat")

        replay = self._complete(client, "n-late-subscriber", {})

        assert replay.status_code == 200
        assert client.notif_repo.notifications("u2") == []
        assert client.publisher.published == []

    def test_delivery_status_update_failure_does_not_fail_complete(self, client):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "n-delivery-state")
        client.notif_repo.mark_delivered = lambda _notification: (_ for _ in ()).throw(
            RuntimeError("DynamoDB update failed")
        )

        response = self._complete(client, "n-delivery-state", {"wombat": 1})

        assert response.status_code == 200
        assert len(client.notif_repo.pending_for_file("n-delivery-state")) == 1
