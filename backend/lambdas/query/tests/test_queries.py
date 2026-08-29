"""Tests for the database & query API (Member D).

Split into pure-logic unit tests (no framework) and endpoint tests that run the
real FastAPI app against a fresh SQLite database via dependency overrides.
"""

from __future__ import annotations

import json
import sqlite3
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
    StorageClientError,
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
    def test_legacy_processing_callbacks_are_disabled_by_default(self):
        assert main.Settings().allow_legacy_processing_callbacks is False

    def test_sqlite_repository_migrates_existing_processing_schema(self, tmp_path):
        database = tmp_path / "legacy.db"
        connection = sqlite3.connect(database)
        connection.execute(
            """
            CREATE TABLE files (
                file_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                file_type TEXT NOT NULL, object_key TEXT NOT NULL,
                thumbnail_key TEXT, filename TEXT NOT NULL DEFAULT '',
                content_type TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '{}',
                detections_json TEXT NOT NULL DEFAULT '[]',
                model_version TEXT NOT NULL DEFAULT '', checksum TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed', error_code TEXT,
                message TEXT, processing_sequencer TEXT, lease_expires_at TEXT,
                upload_time TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy",
                "u1",
                "image",
                "originals/u1/legacy.jpg",
                None,
                "legacy.jpg",
                "image/jpeg",
                1,
                "{}",
                "[]",
                "",
                "sha256:legacy",
                "processing",
                None,
                None,
                "seq-1",
                "2026-08-29T00:15:00+00:00",
                "2026-08-29T00:00:00+00:00",
            ),
        )
        connection.commit()
        connection.close()

        repository = SQLiteRepository(str(database))

        record = repository.get("legacy")
        assert record.status == "processing"
        assert record.processing_lease_token is None
        assert "processing_lease_token" in {
            row[1] for row in repository._conn.execute("PRAGMA table_info(files)")
        }
        repository.add(
            FileRecord(
                file_id="after-migration",
                user_id="u1",
                file_type="image",
                object_key="originals/u1/after-migration.jpg",
                checksum="sha256:after-migration",
                status="processing",
                processing_sequencer="seq-2",
                processing_lease_token="m" * 43,
            )
        )
        migrated_insert = repository.get("after-migration")
        assert migrated_insert.processing_sequencer == "seq-2"
        assert migrated_insert.processing_lease_token == "m" * 43

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

    def test_sqlite_notification_add_reports_only_the_atomic_create(self, tmp_path):
        notification_repo = SQLiteNotificationRepository(str(tmp_path / "notifications.db"))
        notification = Notification(
            notification_id="stable",
            user_id="u2",
            file_id="f1",
            species="wombat",
            object_key="originals/f1",
        )

        assert notification_repo.add_notification(notification) is True
        assert notification_repo.add_notification(notification) is False

    def test_sqlite_notification_delete_removes_the_created_notification(self, tmp_path):
        notification_repo = SQLiteNotificationRepository(str(tmp_path / "notifications.db"))
        notification = Notification(
            notification_id="compensated",
            user_id="u2",
            file_id="f1",
            species="wombat",
            object_key="originals/f1",
        )
        assert notification_repo.add_notification(notification) is True

        notification_repo.delete_notification(notification)

        assert notification_repo.notifications("u2") == []
        assert notification_repo.add_notification(notification) is True


class TestInternalAssetAuthorization:
    def test_completed_keys_are_authorized_with_mixed_denials_and_deduplication(self, client):
        completed = FileRecord(file_id='asset-completed', user_id='u1', file_type='image', object_key='originals/u1/file.jpg', thumbnail_key='thumbnails/u1/file.jpg', checksum='asset-completed', status='completed')
        pending = FileRecord(file_id='asset-pending', user_id='u1', file_type='image', object_key='originals/u1/pending.jpg', thumbnail_key=None, checksum='asset-pending', status='processing')
        client.repo.add(completed)
        client.repo.add(pending)
        keys = ['originals/u1/file.jpg', 'thumbnails/u1/file.jpg', 'originals/u1/pending.jpg', 'processing/u1/file.jpg', 'originals/u1/file.jpg', 'originals/u1/missing.jpg']
        response = client.post('/internal/assets/authorize', headers={'X-Internal-Api-Key': INTERNAL_API_KEY}, json={'keys': keys})
        assert response.status_code == 200
        assert response.json() == {'decisions': [
            {'key': 'originals/u1/file.jpg', 'allowed': True},
            {'key': 'thumbnails/u1/file.jpg', 'allowed': True},
            {'key': 'originals/u1/pending.jpg', 'allowed': False, 'code': 'NOT_COMPLETED'},
            {'key': 'processing/u1/file.jpg', 'allowed': False, 'code': 'FORBIDDEN_KEY'},
            {'key': 'originals/u1/missing.jpg', 'allowed': False, 'code': 'NOT_FOUND'},
        ]}

    def test_authorize_requires_internal_key_and_rejects_malformed_batch(self, client):
        assert client.post('/internal/assets/authorize', json={'keys': ['originals/u1/file.jpg']}).status_code == 401
        assert client.post('/internal/assets/authorize', headers={'X-Internal-Api-Key': INTERNAL_API_KEY}, json={'keys': []}).status_code == 422
        assert client.post('/internal/assets/authorize', headers={'X-Internal-Api-Key': INTERNAL_API_KEY}, json={'keys': [f'originals/u1/{"海" * 400}.jpg']}).status_code == 422


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
        assert r.json()["item"] == {
            "file_id": "f1", "file_type": "image",
            "display_key": "thumbnails/f1.jpg", "original_key": "originals/f1",
            "thumbnail_key": "thumbnails/f1.jpg", "can_preview": True,
            "can_manage": True,
        }

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
        assert response.json()["original_key"] == "originals/f1"
        assert response.json()["file_id"] == "f1"
        assert response.json()["item"]["file_type"] == "image"

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

    def test_manual_completed_activation_creates_and_publishes_notification(self, client):
        client.notif_repo.subscribe("u2", "koala")

        response = client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 1},
        )

        assert response.status_code == 200
        notifications = client.notif_repo.notifications("u2")
        assert [(item.file_id, item.species) for item in notifications] == [
            ("f1", "koala")
        ]
        assert [(item.file_id, item.species) for item in client.publisher.published] == [
            ("f1", "koala")
        ]

    def test_manual_add_to_positive_tag_increases_count_without_notification(self, client):
        client.notif_repo.subscribe("u2", "wombat")

        response = client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["wombat"], "operation": 1},
        )

        assert response.status_code == 200
        assert client.repo.get("f1").tags["wombat"] == 2
        assert client.notif_repo.notifications("u2") == []
        assert client.publisher.published == []

    def test_manual_repeat_add_does_not_duplicate_or_republish_notification(self, client):
        client.notif_repo.subscribe("u2", "koala")
        payload = {"keys": ["originals/f1"], "tags": ["koala"], "operation": 1}

        assert client.post("/tags/edit", json=payload).status_code == 200
        assert client.post("/tags/edit", json=payload).status_code == 200

        assert client.repo.get("f1").tags["koala"] == 2
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.publisher.published) == 1

    def test_manual_remove_does_not_create_or_publish_notification(self, client):
        client.notif_repo.subscribe("u2", "wombat")

        response = client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["wombat"], "operation": 0},
        )

        assert response.status_code == 200
        assert "wombat" not in client.repo.get("f1").tags
        assert client.notif_repo.notifications("u2") == []
        assert client.publisher.published == []

    def test_manual_add_from_zero_count_activates_notification(self, client):
        client.repo.update_tags("f1", {"wombat": 0})
        client.notif_repo.subscribe("u2", "wombat")

        response = client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["wombat"], "operation": 1},
        )

        assert response.status_code == 200
        assert client.repo.get("f1").tags == {"wombat": 1}
        assert [(item.file_id, item.species) for item in client.notif_repo.notifications("u2")] == [
            ("f1", "wombat")
        ]
        assert len(client.publisher.published) == 1

    def test_manual_add_still_non_positive_does_not_activate_notification(self, client):
        client.repo.update_tags("f1", {"wombat": -1})
        client.notif_repo.subscribe("u2", "wombat")

        response = client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["wombat"], "operation": 1},
        )

        assert response.status_code == 200
        assert client.repo.get("f1").tags == {"wombat": 0}
        assert client.notif_repo.notifications("u2") == []
        assert client.publisher.published == []

    def test_manual_alias_and_duplicate_tags_activate_once(self, client):
        client.repo.update_tags("f1", {"wombat": 0})
        client.notif_repo.subscribe("u2", "wombat")

        response = client.post(
            "/tags/edit",
            json={
                "keys": ["originals/f1"],
                "tags": ["Vombatus_ursinus", "wombat", "wombat"],
                "operation": 1,
            },
        )

        assert response.status_code == 200
        assert client.repo.get("f1").tags == {"wombat": 1}
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.publisher.published) == 1

    def test_manual_notification_publish_failure_keeps_pending_and_tag_update(self, client):
        client.notif_repo.subscribe("u2", "koala")

        def fail_publish(_notification):
            raise RuntimeError("temporary SNS outage")

        client.publisher.publish = fail_publish
        response = client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 1},
        )

        assert response.status_code == 200
        assert client.repo.get("f1").tags["koala"] == 1
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.notif_repo.pending_for_file("f1")) == 1

    def test_manual_repeat_add_does_not_replay_existing_pending_notification(self, client):
        client.notif_repo.subscribe("u2", "koala")
        attempts = []

        def fail_publish(notification):
            attempts.append(notification.notification_id)
            raise RuntimeError("temporary SNS outage")

        client.publisher.publish = fail_publish
        payload = {"keys": ["originals/f1"], "tags": ["koala"], "operation": 1}
        assert client.post("/tags/edit", json=payload).status_code == 200

        client.publisher.publish = lambda notification: attempts.append(
            notification.notification_id
        )
        assert client.post("/tags/edit", json=payload).status_code == 200

        assert len(attempts) == 1
        assert len(client.notif_repo.pending_for_file("f1")) == 1

    @pytest.mark.parametrize("delivery_pending", [False, True], ids=["delivered", "pending"])
    def test_manual_remove_then_readd_never_republishes_existing_notification(
        self, client, delivery_pending
    ):
        client.notif_repo.subscribe("u2", "koala")
        attempts = []
        if delivery_pending:
            def fail_publish(notification):
                attempts.append(notification.notification_id)
                raise RuntimeError("temporary SNS outage")

            client.publisher.publish = fail_publish

        payload = {"keys": ["originals/f1"], "tags": ["koala"], "operation": 1}
        assert client.post("/tags/edit", json=payload).status_code == 200
        assert client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 0},
        ).status_code == 200
        if delivery_pending:
            client.publisher.publish = lambda notification: attempts.append(
                notification.notification_id
            )
        assert client.post("/tags/edit", json=payload).status_code == 200

        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(attempts) == (1 if delivery_pending else 0)
        assert len(client.publisher.published) == (0 if delivery_pending else 1)

    def test_manual_readd_notifies_late_subscriber_without_replaying_old_pending(self, client):
        client.notif_repo.subscribe("u2", "koala")
        attempts = []

        def fail_publish(notification):
            attempts.append(notification.user_id)
            raise RuntimeError("temporary SNS outage")

        client.publisher.publish = fail_publish
        payload = {"keys": ["originals/f1"], "tags": ["koala"], "operation": 1}
        assert client.post("/tags/edit", json=payload).status_code == 200
        assert client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 0},
        ).status_code == 200
        client.notif_repo.subscribe("u3", "koala")
        client.publisher.publish = lambda notification: attempts.append(notification.user_id)

        assert client.post("/tags/edit", json=payload).status_code == 200

        assert attempts == ["u2", "u3"]
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.notif_repo.notifications("u3")) == 1

    def test_manual_activation_publishes_only_its_new_species_not_old_pending(self, client):
        client.repo.update_tags("f1", {"wombat": 0})
        client.notif_repo.subscribe("u2", "wombat")
        attempts = []

        def fail_publish(notification):
            attempts.append(notification.species)
            raise RuntimeError("temporary SNS outage")

        client.publisher.publish = fail_publish
        assert client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["wombat"], "operation": 1},
        ).status_code == 200
        client.notif_repo.subscribe("u3", "koala")
        client.publisher.publish = lambda notification: attempts.append(notification.species)

        assert client.post(
            "/tags/edit",
            json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 1},
        ).status_code == 200

        assert attempts == ["wombat", "koala"]
        assert len(client.notif_repo.pending_for_file("f1")) == 1

    def test_manual_inbox_failure_restores_tags_and_fails_request(self, client):
        client.notif_repo.subscribe("u2", "koala")
        original_tags = dict(client.repo.get("f1").tags)

        def fail_add_notification(_notification):
            raise RuntimeError("notification database unavailable")

        client.notif_repo.add_notification = fail_add_notification

        with pytest.raises(RuntimeError, match="notification database unavailable"):
            client.post(
                "/tags/edit",
                json={"keys": ["originals/f1"], "tags": ["koala"], "operation": 1},
            )

        assert client.repo.get("f1").tags == original_tags
        assert client.publisher.published == []

    def test_manual_partial_inbox_failure_compensates_created_notifications_and_retries(
        self, client
    ):
        client.notif_repo.subscribe("u2", "koala")
        client.notif_repo.subscribe("u3", "koala")
        original_tags = dict(client.repo.get("f1").tags)
        original_add_notification = client.notif_repo.add_notification
        add_attempts = 0

        def fail_second_notification(notification):
            nonlocal add_attempts
            add_attempts += 1
            if add_attempts == 2:
                raise RuntimeError("second notification write failed")
            return original_add_notification(notification)

        client.notif_repo.add_notification = fail_second_notification
        payload = {"keys": ["originals/f1"], "tags": ["koala"], "operation": 1}

        with pytest.raises(RuntimeError, match="second notification write failed"):
            client.post("/tags/edit", json=payload)

        assert client.repo.get("f1").tags == original_tags
        assert client.notif_repo.notifications("u2") == []
        assert client.notif_repo.notifications("u3") == []
        assert client.publisher.published == []

        client.notif_repo.add_notification = original_add_notification
        response = client.post("/tags/edit", json=payload)

        assert response.status_code == 200
        assert [notification.user_id for notification in client.publisher.published] == [
            "u2",
            "u3",
        ]
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.notif_repo.notifications("u3")) == 1

    def test_manual_add_on_non_completed_record_does_not_notify(self, client):
        client.repo.add(
            FileRecord(
                file_id="manual-processing",
                user_id="u1",
                file_type="image",
                object_key="originals/manual-processing",
                checksum="sha256:manual-processing",
                status="processing",
            )
        )
        client.notif_repo.subscribe("u2", "koala")

        response = client.post(
            "/tags/edit",
            json={
                "keys": ["originals/manual-processing"],
                "tags": ["koala"],
                "operation": 1,
            },
        )

        assert response.status_code == 200
        assert client.repo.get("manual-processing").tags == {"koala": 1}
        assert client.notif_repo.notifications("u2") == []
        assert client.publisher.published == []

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

    def test_delete_storage_failure_leaves_metadata_hidden_for_retry(self, client):
        def fail_delete(_user_id, _keys):
            raise RuntimeError("storage unavailable")

        client.storage.delete = fail_delete

        with pytest.raises(RuntimeError, match="storage unavailable"):
            client.post("/files/delete", json={"keys": ["originals/f1"]})

        record = client.repo.get("f1")
        assert record.status == "deleting"
        assert len(record.deletion_attempt_token) >= 32

    def test_ambiguous_partial_storage_delete_retries_with_a_new_attempt_token(
        self, client
    ):
        attempt_tokens = []
        attempts = 0

        def ambiguous_then_success(_user_id, keys):
            nonlocal attempts
            attempts += 1
            attempt_tokens.append(client.repo.get("f1").deletion_attempt_token)
            if attempts == 1:
                client.storage.deleted.append(keys[0])
                raise StorageClientError("storage response lost after partial delete")
            client.storage.deleted.extend(keys)

        client.storage.delete = ambiguous_then_success

        first = client.post("/files/delete", json={"keys": ["originals/f1"]})
        retry = client.post("/files/delete", json={"keys": ["originals/f1"]})

        assert first.status_code == 502
        assert retry.status_code == 200
        assert attempt_tokens[0] != attempt_tokens[1]
        assert all(len(token) >= 32 for token in attempt_tokens)
        assert client.repo.get("f1") is None

    def test_older_delete_attempt_cannot_finalize_a_newer_attempt(self, client):
        old_attempt = "o" * 43
        new_attempt = "n" * 43
        assert client.repo.begin_delete("f1", "u1", old_attempt) is True
        assert client.repo.begin_delete("f1", "u1", new_attempt) is True

        removed_by_old = client.repo.delete_by_ids(
            ["f1"],
            user_id="u1",
            deletion_attempt_tokens={"f1": old_attempt},
        )

        record = client.repo.get("f1")
        assert removed_by_old == 0
        assert record.status == "deleting"
        assert record.deletion_attempt_token == new_attempt
        assert client.repo.delete_by_ids(
            ["f1"],
            user_id="u1",
            deletion_attempt_tokens={"f1": new_attempt},
        ) == 1

    def test_delete_marks_metadata_deleting_before_storage_and_removes_it_on_success(
        self, client
    ):
        observed_statuses = []
        original_delete = client.storage.delete

        def observe_deleting(user_id, keys):
            observed_statuses.append(client.repo.get("f1").status)
            original_delete(user_id, keys)

        client.storage.delete = observe_deleting

        response = client.post("/files/delete", json={"keys": ["originals/f1"]})

        assert response.status_code == 200
        assert observed_statuses == ["deleting"]
        assert client.repo.get("f1") is None

    def test_metadata_delete_failure_stays_hidden_and_retry_converges(self, client):
        client.repo.add(
            FileRecord(
                file_id="delete-retry",
                user_id="u1",
                file_type="image",
                object_key="originals/u1/delete-retry.jpg",
                thumbnail_key="thumbnails/u1/delete-retry.jpg",
                tags={"wombat": 1},
                checksum="sha256:delete-retry",
                status="completed",
            )
        )
        original_delete_by_ids = client.repo.delete_by_ids
        attempts = 0

        def fail_first_metadata_delete(file_ids, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("metadata delete failed")
            return original_delete_by_ids(file_ids, **kwargs)

        client.repo.delete_by_ids = fail_first_metadata_delete

        with pytest.raises(RuntimeError, match="metadata delete failed"):
            client.post(
                "/files/delete", json={"keys": ["originals/u1/delete-retry.jpg"]}
            )

        assert client.repo.get("delete-retry").status == "deleting"
        reacquire = client.post(
            "/internal/files/delete-retry/processing",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={
                "user_id": "u1",
                "object_key": "originals/u1/delete-retry.jpg",
                "sequencer": "late-s3-event",
            },
        )
        assert reacquire.json() == {
            "should_process": False,
            "state": "lease_active",
        }
        assert client.repo.get("delete-retry").status == "deleting"
        query = client.post("/query/by-species", json={"species": "wombat"})
        assert "thumbnails/u1/delete-retry.jpg" not in query.json()["results"]
        authorization = client.post(
            "/internal/assets/authorize",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={"keys": ["originals/u1/delete-retry.jpg"]},
        )
        assert authorization.json()["decisions"] == [
            {
                "key": "originals/u1/delete-retry.jpg",
                "allowed": False,
                "code": "NOT_COMPLETED",
            }
        ]

        retry = client.post(
            "/files/delete", json={"keys": ["originals/u1/delete-retry.jpg"]}
        )

        assert retry.status_code == 200
        assert retry.json()["deleted_db_records"] == 1
        assert client.repo.get("delete-retry") is None
        assert client.storage.deleted.count("originals/u1/delete-retry.jpg") == 2
        assert client.storage.deleted.count("thumbnails/u1/delete-retry.jpg") == 2

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


def _reacquire_processing(client, file_id):
    headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
    body = {
        "user_id": "u1",
        "object_key": f"originals/{file_id}",
        "sequencer": "seq-1",
    }
    first = client.post(
        f"/internal/files/{file_id}/processing", json=body, headers=headers
    ).json()
    client.repo._conn.execute(
        "UPDATE files SET lease_expires_at=? WHERE file_id=?",
        ("2000-01-01T00:00:00+00:00", file_id),
    )
    client.repo._conn.commit()
    body["sequencer"] = "seq-2"
    second = client.post(
        f"/internal/files/{file_id}/processing", json=body, headers=headers
    ).json()
    return first["lease_token"], second["lease_token"]


def _active_lease_token(client, file_id):
    record = client.repo.get(file_id)
    if record.status == "processing":
        return record.processing_lease_token
    if record.status == "completed":
        return "completed-replay-token".ljust(32, "x")
    response = client.post(
        f"/internal/files/{file_id}/processing",
        headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
        json={
            "user_id": record.user_id,
            "object_key": record.object_key,
            "sequencer": "test-sequencer",
        },
    )
    return response.json()["lease_token"]


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
        client.repo.mark_processing("r2", "test-setup", main.utcnow())
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
        client.repo.mark_processing("delete-old", "test-setup", main.utcnow())
        client.repo.mark_completed(
            "delete-old", "originals/delete-old", None, "image", {}, [], "v1"
        )
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
        assert acquired["should_process"] is True
        assert acquired["state"] == "acquired"
        assert len(acquired["lease_token"]) >= 32
        assert active == {"should_process": False, "state": "lease_active"}

    def test_processing_reacquisition_returns_a_new_unguessable_lease_token(
        self, client
    ):
        _reserve(client, "lease-token")
        body = {
            "user_id": "u1",
            "object_key": "originals/lease-token",
            "sequencer": "seq-1",
        }
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}

        first = client.post(
            "/internal/files/lease-token/processing", json=body, headers=headers
        ).json()
        client.repo._conn.execute(
            "UPDATE files SET lease_expires_at=? WHERE file_id=?",
            ("2000-01-01T00:00:00+00:00", "lease-token"),
        )
        client.repo._conn.commit()
        body["sequencer"] = "seq-2"
        second = client.post(
            "/internal/files/lease-token/processing", json=body, headers=headers
        ).json()

        assert first["state"] == second["state"] == "acquired"
        assert len(first["lease_token"]) >= 32
        assert len(second["lease_token"]) >= 32
        assert first["lease_token"] != second["lease_token"]
        assert client.repo.get("lease-token").processing_lease_token == second["lease_token"]

    def test_stale_completion_cannot_mutate_a_reacquired_processing_lease(self, client):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "stale-complete")
        stale_token, active_token = _reacquire_processing(client, "stale-complete")
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
        stale_payload = {
            "user_id": "u1",
            "file_type": "image",
            "original_key": "originals/stale-complete",
            "tags": {"wombat": 9},
            "model_version": "stale",
            "lease_token": stale_token,
        }

        response = client.put(
            "/internal/files/stale-complete/complete",
            json=stale_payload,
            headers=headers,
        )

        assert response.status_code == 200
        record = client.repo.get("stale-complete")
        assert record.status == "processing"
        assert record.processing_lease_token == active_token
        assert record.tags == {}
        assert client.notif_repo.notifications("u2") == []

        fresh_payload = {
            **stale_payload,
            "tags": {"dingo": 1},
            "model_version": "fresh",
            "lease_token": active_token,
        }
        assert client.put(
            "/internal/files/stale-complete/complete",
            json=fresh_payload,
            headers=headers,
        ).status_code == 200
        completed = client.repo.get("stale-complete")
        assert completed.status == "completed"
        assert completed.tags == {"dingo": 1}
        assert completed.model_version == "fresh"

    @pytest.mark.parametrize("callback", ["complete", "failed"])
    def test_tokenless_processing_callbacks_are_rejected_by_default(
        self, client, callback
    ):
        file_id = f"tokenless-default-{callback}"
        _reserve(client, file_id)
        _active_lease_token(client, file_id)
        payload = (
            {
                "user_id": "u1",
                "file_type": "image",
                "original_key": f"originals/{file_id}",
                "tags": {"wombat": 1},
                "model_version": "legacy",
            }
            if callback == "complete"
            else {"user_id": "u1", "error_code": "LEGACY", "message": "old B"}
        )

        response = client.put(
            f"/internal/files/{file_id}/{callback}",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json=payload,
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PROCESSING_LEASE_TOKEN_REQUIRED"
        assert client.repo.get(file_id).status == "processing"

    @pytest.mark.parametrize(
        ("callback", "expected_status"),
        [("complete", "completed"), ("failed", "failed")],
    )
    def test_compatibility_flag_accepts_legacy_tokenless_callbacks(
        self, client, callback, expected_status
    ):
        file_id = f"tokenless-enabled-{callback}"
        _reserve(client, file_id)
        _active_lease_token(client, file_id)
        main.app.dependency_overrides[main.get_settings] = lambda: SimpleNamespace(
            internal_api_key=INTERNAL_API_KEY,
            query_input_bucket="private-media",
            allow_legacy_processing_callbacks=True,
        )
        payload = (
            {
                "user_id": "u1",
                "file_type": "image",
                "original_key": f"originals/{file_id}",
                "tags": {"wombat": 1},
                "model_version": "legacy",
            }
            if callback == "complete"
            else {"user_id": "u1", "error_code": "LEGACY", "message": "old B"}
        )

        response = client.put(
            f"/internal/files/{file_id}/{callback}",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json=payload,
        )

        assert response.status_code == 200
        assert client.repo.get(file_id).status == expected_status

    @pytest.mark.parametrize("callback", ["complete", "failed"])
    @pytest.mark.parametrize(
        "status", ["pending_upload", "failed", "completed", "deleting"]
    )
    def test_legacy_tokenless_callbacks_never_mutate_non_processing_records(
        self, client, callback, status
    ):
        file_id = f"legacy-{callback}-{status}"
        client.repo.add(
            FileRecord(
                file_id=file_id,
                user_id="u1",
                file_type="image",
                object_key=f"originals/{file_id}",
                tags={"dingo": 1},
                checksum=f"sha256:{file_id}",
                status=status,
                error_code="ORIGINAL",
                message="original diagnostic",
            )
        )
        main.app.dependency_overrides[main.get_settings] = lambda: SimpleNamespace(
            internal_api_key=INTERNAL_API_KEY,
            query_input_bucket="private-media",
            allow_legacy_processing_callbacks=True,
        )
        client.notif_repo.subscribe("u2", "dingo")
        payload = (
            {
                "user_id": "u1",
                "file_type": "image",
                "original_key": f"originals/{file_id}",
                "tags": {"wombat": 99},
                "model_version": "late-legacy-worker",
            }
            if callback == "complete"
            else {
                "user_id": "u1",
                "error_code": "LATE_LEGACY_WORKER",
                "message": "must not replace the original",
            }
        )

        response = client.put(
            f"/internal/files/{file_id}/{callback}",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json=payload,
        )

        record = client.repo.get(file_id)
        assert response.status_code == 200
        assert record.status == status
        assert record.tags == {"dingo": 1}
        assert record.error_code == "ORIGINAL"
        assert record.message == "original diagnostic"
        assert client.notif_repo.notifications("u2") == []

    def test_stale_failure_cannot_mutate_a_reacquired_processing_lease(self, client):
        _reserve(client, "stale-failure")
        stale_token, active_token = _reacquire_processing(client, "stale-failure")
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}

        response = client.put(
            "/internal/files/stale-failure/failed",
            headers=headers,
            json={
                "user_id": "u1",
                "error_code": "STALE",
                "message": "late worker",
                "lease_token": stale_token,
            },
        )

        assert response.status_code == 200
        record = client.repo.get("stale-failure")
        assert record.status == "processing"
        assert record.processing_lease_token == active_token
        assert record.error_code is None

        assert client.put(
            "/internal/files/stale-failure/failed",
            headers=headers,
            json={
                "user_id": "u1",
                "error_code": "FRESH",
                "message": "active worker",
                "lease_token": active_token,
            },
        ).status_code == 200
        failed = client.repo.get("stale-failure")
        assert failed.status == "failed"
        assert failed.error_code == "FRESH"

    def test_processing_completed_returns_false(self, client):
        _reserve(client, "p2")
        client.repo.mark_processing("p2", "test-setup", main.utcnow())
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
        payload["lease_token"] = _active_lease_token(client, "c1")
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
        assert client.put("/internal/files/c1/complete", json=payload, headers=headers).status_code == 200
        rec = client.repo.get("c1")
        assert rec.status == "completed" and rec.tags == {"wombat": 2}
        assert client.put("/internal/files/c1/complete", json=payload, headers=headers).status_code == 200

    def test_complete_normalizes_tags_and_detection_species_before_writing(self, client):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "normalize")
        lease_token = _active_lease_token(client, "normalize")

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
                "lease_token": lease_token,
            },
        )

        assert response.status_code == 200
        record = client.repo.get("normalize")
        assert record.tags == {"wombat": 2}
        assert record.detections == [{"species": "wombat", "confidence": 0.94}]
        assert [n.species for n in client.notif_repo.notifications("u2")] == ["wombat"]

    def test_failed_truncates_and_idempotent(self, client):
        _reserve(client, "e1")
        lease_token = _active_lease_token(client, "e1")
        payload = {"user_id": "u1", "error_code": "INVALID_MEDIA", "message": "x" * 500, "status": "failed", "lease_token": lease_token}
        headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
        assert client.put("/internal/files/e1/failed", json=payload, headers=headers).status_code == 200
        rec = client.repo.get("e1")
        assert rec.status == "failed" and len(rec.message) == 240
        payload2 = {"user_id": "u1", "error_code": "INVALID_MEDIA", "message": "again", "status": "failed", "lease_token": lease_token}
        assert client.put("/internal/files/e1/failed", json=payload2, headers=headers).status_code == 200

    def test_failed_does_not_downgrade_completed(self, client):
        _reserve(client, "e2")
        client.repo.mark_processing("e2", "test-setup", main.utcnow())
        client.repo.mark_completed("e2", "originals/e2", None, "image", {}, [], "v1")
        client.put(
            "/internal/files/e2/failed",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={"user_id": "u1", "error_code": "X", "message": "m", "status": "failed", "lease_token": "completed-token".ljust(32, "x")},
        )
        assert client.repo.get("e2").status == "completed"

    def test_sqlite_mark_failed_condition_blocks_stale_callback_downgrade(self, client):
        _reserve(client, "e2-race")
        client.repo.mark_processing("e2-race", "test-setup", main.utcnow())
        client.repo.mark_completed(
            "e2-race", "originals/e2-race", None, "image", {}, [], "v1"
        )

        client.repo.mark_failed("e2-race", "INFERENCE_FAILED", "late callback")

        assert client.repo.get("e2-race").status == "completed"

    def test_sqlite_first_complete_wins_concurrent_callback_race(self, client):
        _reserve(client, "complete-race")
        client.repo.mark_processing("complete-race", "test-setup", main.utcnow())
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
                        "lease_token": "auth-complete-token".ljust(32, "x"),
                    },
                )
            else:
                response = client.put(
                    "/internal/files/auth-failed/failed",
                    headers=headers,
                    json={"user_id": "u1", "error_code": "INVALID_MEDIA", "lease_token": "auth-failed-token".ljust(32, "x")},
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
        if route in {"complete", "failed"}:
            payload = {**payload, "lease_token": "conflict-token".ljust(32, "x")}
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
        lease_token = _active_lease_token(client, file_id)
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
                "lease_token": lease_token,
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

    def test_inbox_is_not_written_until_fenced_completion_succeeds(
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

        assert client.repo.get("n-mark-fails").status == "processing"
        assert client.notif_repo.pending_for_file("n-mark-fails") == []

        client.repo.mark_completed = original_mark_completed
        replay = self._complete(client, "n-mark-fails", {"wombat": 1})

        assert replay.status_code == 200
        assert len(client.notif_repo.notifications("u2")) == 1
        assert len(client.publisher.published) == 1

    def test_next_delivery_recovers_inbox_after_completion_cas_wins(
        self, client
    ):
        from pathlib import Path
        import sys

        media_processing_root = Path(__file__).resolve().parents[2] / "media-processing"
        if str(media_processing_root) not in sys.path:
            sys.path.insert(0, str(media_processing_root))
        from media_pipeline.pipeline import MediaPipeline

        file_id = "n-begin-recovers"
        object_key = f"originals/u1/{file_id}/wombat.jpg"
        client.notif_repo.subscribe("u2", "wombat")
        reserve = client.post(
            "/internal/uploads/reserve",
            headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            json={
                "file_id": file_id,
                "user_id": "u1",
                "checksum": "sha256:n-begin-recovers",
                "filename": "wombat.jpg",
                "file_type": "image",
                "content_type": "image/jpeg",
                "size_bytes": 100,
                "object_key": object_key,
            },
        )
        assert reserve.status_code == 201
        original_add_notification = client.notif_repo.add_notification
        attempts = 0

        def fail_first_inbox_write(notification):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("notification store unavailable")
            return original_add_notification(notification)

        client.notif_repo.add_notification = fail_first_inbox_write

        class _Metadata:
            headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}

            def begin_processing(self, target_file_id, payload):
                response = client.post(
                    f"/internal/files/{target_file_id}/processing",
                    headers=self.headers,
                    json=payload,
                )
                assert response.status_code == 200
                return response.json()

            def complete(self, target_file_id, payload):
                response = client.put(
                    f"/internal/files/{target_file_id}/complete",
                    headers=self.headers,
                    json=payload,
                )
                assert response.status_code == 200

            def fail(self, target_file_id, payload):
                response = client.put(
                    f"/internal/files/{target_file_id}/failed",
                    headers=self.headers,
                    json=payload,
                )
                assert response.status_code == 200

        class _Storage:
            def __init__(self):
                self.content_type_calls = 0

            def get_content_type(self, _bucket, _key):
                self.content_type_calls += 1
                return "image/jpeg"

            def download(self, _bucket, _key, destination):
                Path(destination).write_bytes(b"source")

            def upload(self, _bucket, _key, _source, _content_type):
                return None

            def presign_get(self, _bucket, key):
                return f"https://signed.example/{key}"

        class _Inference:
            def __init__(self):
                self.calls = []

            def infer(self, payload):
                self.calls.append(payload)
                return {
                    "tags": {"wombat": 2},
                    "detections": [],
                    "model_version": "v1",
                }

        storage = _Storage()
        inference = _Inference()
        pipeline = MediaPipeline(
            storage=storage,
            metadata=_Metadata(),
            inference=inference,
            create_thumbnail=lambda _source, target: Path(target).write_bytes(
                b"thumbnail"
            ),
        )
        s3_event = {
            "s3": {
                "bucket": {"name": "private-media"},
                "object": {"key": object_key, "sequencer": "first-delivery"},
            }
        }

        with pytest.raises(RuntimeError, match="notification store unavailable"):
            pipeline.process_record(s3_event)

        completed = client.repo.get(file_id)
        assert completed.status == "completed"
        assert completed.tags == {"wombat": 2}
        assert client.notif_repo.notifications("u2") == []

        s3_event["s3"]["object"]["sequencer"] = "next-delivery"
        next_delivery = pipeline.process_record(s3_event)

        assert next_delivery == {"status": "skipped", "file_id": file_id}
        assert storage.content_type_calls == 1
        assert len(inference.calls) == 1
        notifications = client.notif_repo.notifications("u2")
        assert [(item.species, item.object_key) for item in notifications] == [
            ("wombat", object_key)
        ]
        assert len(client.publisher.published) == 1

    def test_stale_completion_cannot_write_notifications_after_lease_reacquisition(
        self, client
    ):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "n-stale-complete")
        original_mark_completed = client.repo.mark_completed
        newer_token = "n" * 43

        def reacquire_before_compare_and_swap(*args, **kwargs):
            client.repo._conn.execute(
                "UPDATE files SET lease_expires_at=? WHERE file_id=?",
                ("2000-01-01T00:00:00+00:00", "n-stale-complete"),
            )
            client.repo._conn.commit()
            now = main.utcnow()
            assert client.repo.try_acquire_processing(
                "n-stale-complete",
                "newer-sequencer",
                now,
                now + main.timedelta(seconds=60),
                newer_token,
            ) == "acquired"
            return original_mark_completed(*args, **kwargs)

        client.repo.mark_completed = reacquire_before_compare_and_swap

        response = self._complete(client, "n-stale-complete", {"wombat": 1})

        record = client.repo.get("n-stale-complete")
        assert response.status_code == 200
        assert record.status == "processing"
        assert record.processing_lease_token == newer_token
        assert client.notif_repo.notifications("u2") == []
        assert client.publisher.published == []

    def test_completed_replay_ensures_notifications_from_stored_metadata(self, client):
        _reserve(client, "n-late-subscriber")
        self._complete(client, "n-late-subscriber", {"wombat": 1})
        client.notif_repo.subscribe("u2", "wombat")
        client.notif_repo.subscribe("u2", "fox")

        replay = self._complete(client, "n-late-subscriber", {"fox": 99})

        assert replay.status_code == 200
        notifications = client.notif_repo.notifications("u2")
        assert [(item.species, item.object_key) for item in notifications] == [
            ("wombat", "originals/n-late-subscriber")
        ]
        assert len(client.publisher.published) == 1

    def test_delivery_status_update_failure_does_not_fail_complete(self, client):
        client.notif_repo.subscribe("u2", "wombat")
        _reserve(client, "n-delivery-state")
        client.notif_repo.mark_delivered = lambda _notification: (_ for _ in ()).throw(
            RuntimeError("DynamoDB update failed")
        )

        response = self._complete(client, "n-delivery-state", {"wombat": 1})

        assert response.status_code == 200
        assert len(client.notif_repo.pending_for_file("n-delivery-state")) == 1
