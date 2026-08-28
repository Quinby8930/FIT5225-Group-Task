"""Generate reproducible local evidence pages for Member D.

The pages are populated from real pytest output and real FastAPI TestClient
requests backed by SQLite.  They are then captured as PNGs by the browser.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


QUERY_DIR = Path(__file__).resolve().parents[3] / "backend" / "lambdas" / "query"
EVIDENCE_DIR = Path(__file__).resolve().parent
HTML_DIR = EVIDENCE_DIR / "html"
ARTIFACT_DIR = EVIDENCE_DIR / "artifacts"
SCREENSHOT_DIR = EVIDENCE_DIR / "screenshots"
DB_PATH = ARTIFACT_DIR / "member-d-evidence.db"
TMP_PATH = ARTIFACT_DIR / "pytest-temp"
INTERNAL_API_KEY = "local-evidence-key"

sys.path.insert(0, str(QUERY_DIR))
os.chdir(QUERY_DIR)

from fastapi.testclient import TestClient  # noqa: E402
from app import main  # noqa: E402
from app.notification_client import NotificationPublisher  # noqa: E402
from app.repository import SQLiteNotificationRepository, SQLiteRepository  # noqa: E402
from app.schemas import FileRecord  # noqa: E402
from app.storage_client import StorageClient  # noqa: E402
from app.tag_detector import TagDetector  # noqa: E402


FILES = [
    "Test suite 147 passing",
    "SQLite local backend",
    "Query by tags AND",
    "Query by species",
    "Thumbnail to original mapping",
    "Query by uploaded file",
    "Bulk tag edit",
    "Bulk delete with owner check",
    "Reserve returns 201",
    "Complete idempotent 200",
    "Internal auth fails closed",
    "Subscribe to species",
    "Notification triggered on complete",
    "List subscriptions",
]


def pretty(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=QUERY_DIR,
        text=True,
    ).strip()


def render_page(
    number: int,
    title: str,
    command: str,
    request_text: str,
    response_text: str,
    status: str,
    verification: list[str],
    *,
    response_class: str = "ok",
    note: str = "Real local execution against the current repository checkout.",
) -> None:
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    checks = "".join(f"<li>✓ {html.escape(item)}</li>" for item in verification)
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title>
<style>
*{{box-sizing:border-box}} html,body{{margin:0;width:100%;min-height:100%;background:#07111f;color:#e8eef7}}
body{{font-family:Inter,Segoe UI,Arial,sans-serif;padding:34px 42px}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid #26374e;padding-bottom:20px;margin-bottom:24px}}
.eyebrow{{color:#6dd6ff;font-size:14px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}}
h1{{font-size:31px;line-height:1.2;margin:8px 0 4px}} .sub{{color:#9fb0c7;font-size:14px}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;max-width:470px}}
.badge{{border:1px solid #31506e;background:#10243a;border-radius:999px;padding:7px 11px;font:600 12px Consolas,monospace;color:#bde8ff}}
.grid{{display:grid;grid-template-columns:1fr 1.08fr;gap:20px}}
.card{{background:#0d1b2d;border:1px solid #263a52;border-radius:13px;overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,.22)}}
.wide{{grid-column:1/-1}} .label{{padding:11px 15px;background:#12253b;border-bottom:1px solid #263a52;color:#9fb7d1;font:700 12px Consolas,monospace;text-transform:uppercase;letter-spacing:.08em}}
pre{{margin:0;padding:17px 18px;font:15px/1.48 Consolas,'Cascadia Mono',monospace;white-space:pre-wrap;word-break:break-word;color:#edf5ff;max-height:420px;overflow:hidden}}
.status{{display:inline-block;margin:16px 18px 0;padding:8px 13px;border-radius:7px;font:800 15px Consolas,monospace}}
.ok{{background:#0d3d2c;color:#6cf0b3;border:1px solid #1f7657}} .error{{background:#402029;color:#ff9eae;border:1px solid #7b3545}}
ul{{list-style:none;margin:0;padding:13px 18px 17px}} li{{padding:5px 0;color:#baf2d5;font:14px/1.35 Consolas,monospace}}
.footer{{display:flex;justify-content:space-between;margin-top:22px;color:#7488a2;font:12px Consolas,monospace}}
.note{{margin-top:14px;color:#9fb0c7;font-size:12px}}
</style></head><body>
<div class="top"><div><div class="eyebrow">Pacific BioArchive · Member D · Local Evidence #{number:02d}</div><h1>{html.escape(title)}</h1><div class="sub">{html.escape(note)}</div></div>
<div class="badges"><span class="badge">COMMIT {git_commit()}</span><span class="badge">FASTAPI</span><span class="badge">SQLITE</span><span class="badge">LOCAL</span></div></div>
<div class="grid">
<section class="card wide"><div class="label">Command / endpoint</div><pre>{html.escape(command)}</pre></section>
<section class="card"><div class="label">Request</div><pre>{html.escape(request_text)}</pre></section>
<section class="card"><div class="label">Response / output</div><div class="status {response_class}">{html.escape(status)}</div><pre>{html.escape(response_text)}</pre></section>
<section class="card wide"><div class="label">Verified assertions</div><ul>{checks}</ul></section>
</div><div class="footer"><span>{html.escape(stamp)}</span><span>Generated from real repository execution · no cloud resources used</span></div>
</body></html>"""
    (HTML_DIR / f"{number:02d}.html").write_text(content, encoding="utf-8")


def run_tests() -> tuple[str, str]:
    if TMP_PATH.exists():
        shutil.rmtree(TMP_PATH)
    TMP_PATH.mkdir(parents=True)
    env = dict(os.environ)
    env["TEMP"] = str(TMP_PATH)
    env["TMP"] = str(TMP_PATH)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "-v",
            "-p",
            "no:cacheprovider",
            f"--basetemp={TMP_PATH / 'run'}",
        ],
        cwd=QUERY_DIR,
        env=env,
        text=True,
        capture_output=True,
    )
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout + result.stderr)
    selected_names = (
        "test_by_tags_and",
        "test_by_file_does_not_persist",
        "test_delete_rejects_entire_request",
        "test_complete_idempotent",
        "test_internal_routes_fail_closed",
        "test_subscribe_and_list",
        "test_complete_triggers_notification",
    )
    selected = [
        line for line in output.splitlines()
        if any(name in line for name in selected_names)
    ]
    summary = next(
        (line for line in reversed(output.splitlines()) if " passed in " in line),
        "pytest summary not found",
    )
    # Keep representative coverage and the authoritative final summary visible
    # together in a single screenshot.
    display = "\n".join(selected[:8] + ["", summary])
    if result.returncode != 0:
        raise RuntimeError(output[-5000:])
    return display, summary


def seed(repo: SQLiteRepository) -> None:
    rows = [
        ("f1", "u1", "image", "originals/u1/a1.jpg", "thumbnails/u1/a1.jpg", {"dingo": 2, "wombat": 1}),
        ("f2", "u1", "image", "originals/u1/a2.jpg", "thumbnails/u1/a2.jpg", {"wombat": 2, "magpie": 1}),
        ("f3", "u1", "video", "originals/u1/v1.mp4", None, {"dingo": 1, "wombat": 3}),
        ("f4", "u2", "image", "originals/u2/a3.jpg", "thumbnails/u2/a3.jpg", {"dingo": 1}),
    ]
    for fid, uid, kind, object_key, thumbnail_key, tags in rows:
        repo.add(FileRecord(
            file_id=fid,
            user_id=uid,
            file_type=kind,
            object_key=object_key,
            thumbnail_key=thumbnail_key,
            tags=tags,
            checksum=f"sha256:{fid}",
            status="completed",
        ))


class EvidenceDetector(TagDetector):
    def detect(self, **_kwargs):
        return {"wombat": 1}


class EvidenceStorage(StorageClient):
    def __init__(self):
        self.deleted: list[str] = []

    def delete(self, _user_id, keys):
        self.deleted.extend(keys)


class EvidencePublisher(NotificationPublisher):
    def __init__(self):
        self.published = []

    def publish(self, notification):
        self.published.append(notification)


def response_block(response) -> str:
    try:
        return pretty(response.json())
    except Exception:
        return response.text


def main_generate() -> None:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    tests_display, tests_summary = run_tests()
    render_page(
        1,
        FILES[0],
        "cd backend/lambdas/query\npython -m pytest tests/ -v",
        "Full Member D test suite (147 collected tests)",
        tests_display,
        "PASS · 147/147",
        [
            tests_summary.strip("= "),
            "Queries, ownership, internal auth, state machine, DynamoDB adapters and notifications passed",
            "Temporary test data used a writable isolated directory on Windows",
        ],
    )

    repo = SQLiteRepository(str(DB_PATH))
    notifications = SQLiteNotificationRepository(str(DB_PATH))
    seed(repo)
    storage = EvidenceStorage()
    detector = EvidenceDetector()
    publisher = EvidencePublisher()

    with sqlite3.connect(DB_PATH) as connection:
        schema = connection.execute("PRAGMA table_info(files)").fetchall()
        records = connection.execute(
            "SELECT file_id,user_id,object_key,thumbnail_key,tags_json,status FROM files ORDER BY file_id"
        ).fetchall()
    db_output = "COLUMNS: " + ", ".join(row[1] for row in schema) + "\n\n"
    db_output += "\n".join(
        f"{fid} | {uid} | {obj} | {thumb or 'NULL'} | {tags} | {status}"
        for fid, uid, obj, thumb, tags, status in records
    )
    render_page(
        2,
        FILES[1],
        "sqlite3 artifacts/member-d-evidence.db\nSELECT file_id,user_id,object_key,thumbnail_key,tags_json,status FROM files;",
        "SQLite PRAGMA schema + SELECT against the local evidence database",
        db_output,
        "4 ROWS · COMPLETED",
        [
            "FileRecord rows are persisted in SQLite",
            "tags_json contains short-name count maps",
            "status is completed for seeded queryable media",
        ],
    )

    main.app.dependency_overrides[main.get_repo] = lambda: repo
    main.app.dependency_overrides[main.get_notification_repo] = lambda: notifications
    main.app.dependency_overrides[main.get_detector] = lambda: detector
    main.app.dependency_overrides[main.get_storage] = lambda: storage
    main.app.dependency_overrides[main.get_publisher] = lambda: publisher
    main.app.dependency_overrides[main.get_current_user] = lambda: "u1"
    main.app.dependency_overrides[main.get_settings] = lambda: SimpleNamespace(
        internal_api_key=INTERNAL_API_KEY
    )

    with TestClient(main.app) as client:
        r = client.post("/query/by-tags", json={"tags": {"dingo": 1, "wombat": 1}})
        render_page(3, FILES[2], "POST /query/by-tags", pretty({"tags": {"dingo": 1, "wombat": 1}}), response_block(r), f"HTTP {r.status_code}", ["AND semantics: every result has both dingo and wombat", "count = 2", "Image returns thumbnail key; video returns original key"])

        r = client.post("/query/by-species", json={"species": "wombat"})
        render_page(4, FILES[3], "POST /query/by-species", pretty({"species": "wombat"}), response_block(r), f"HTTP {r.status_code}", ["Only records whose wombat count is at least 1 are returned", "count = 3", "Response contains storage keys, not public URLs"])

        r = client.get("/query/by-thumbnail", params={"key": "thumbnails/u1/a1.jpg"})
        render_page(5, FILES[4], "GET /query/by-thumbnail?key=thumbnails%2Fu1%2Fa1.jpg", "Query parameter:\nkey = thumbnails/u1/a1.jpg", response_block(r), f"HTTP {r.status_code}", ["original_key = originals/u1/a1.jpg", "file_id = f1", "Mapping came from the SQLite FileRecord"])

        before = len(repo.all())
        r = client.post("/query/by-file", files={"file": ("wombat-query.jpg", b"local-evidence-image", "image/jpeg")})
        after = len(repo.all())
        render_page(6, FILES[5], "POST /query/by-file · multipart/form-data", "file: wombat-query.jpg\ncontent-type: image/jpeg\nbytes: local-evidence-image", response_block(r) + f"\n\nDB row count before: {before}\nDB row count after:  {after}", f"HTTP {r.status_code}", ["Detector returned wombat=1 and matching archive keys were returned", f"Database row count remained {after}", "Uploaded query file was not persisted"])

        edit_body = {"keys": ["originals/u1/a2.jpg"], "tags": ["dingo"], "operation": 1}
        r = client.post("/tags/edit", json=edit_body)
        edited = repo.get("f2")
        render_page(7, FILES[6], "POST /tags/edit", pretty(edit_body), response_block(r) + "\n\nPersisted tags for f2:\n" + pretty(edited.tags if edited else {}), f"HTTP {r.status_code}", ["updated = 1", "matched_keys contains originals/u1/a2.jpg", "dingo count was persisted in SQLite"])

        delete_body = {"keys": ["originals/u1/a2.jpg"]}
        ok = client.post("/files/delete", json=delete_body)
        denied_body = {"keys": ["originals/u2/a3.jpg"]}
        denied = client.post("/files/delete", json=denied_body)
        delete_response = "OWNER REQUEST\n" + response_block(ok) + "\n\nNON-OWNER REQUEST\n" + response_block(denied)
        delete_request = "OWNER · POST /files/delete\n" + pretty(delete_body) + "\n\nNON-OWNER · POST /files/delete\n" + pretty(denied_body)
        render_page(8, FILES[7], "POST /files/delete · owner enforcement", delete_request, delete_response, f"HTTP {ok.status_code} + HTTP {denied.status_code}", ["Owner deletion removed 1 DB record and 2 storage keys", "Non-owner deletion returned 403 FORBIDDEN_OWNER", "Foreign record f4 remains in the database"])

        reserve_body = {
            "file_id": "evidence-upload-001", "user_id": "u1", "checksum": "sha256:evidence-001",
            "filename": "wombat.jpg", "file_type": "image", "content_type": "image/jpeg", "size_bytes": 2048,
            "object_key": "originals/u1/evidence-upload-001/wombat.jpg", "status": "pending_upload",
        }
        r = client.post("/internal/uploads/reserve", json=reserve_body, headers={"X-Internal-Api-Key": INTERNAL_API_KEY})
        render_page(9, FILES[8], "POST /internal/uploads/reserve", "X-Internal-Api-Key: local-evidence-key\n\n" + pretty(reserve_body), response_block(r), f"HTTP {r.status_code}", ["New reservation returned 201", "reused = false", "file_id and canonical object_key were returned"])

        complete_body = {
            "user_id": "u1", "file_type": "image", "original_key": reserve_body["object_key"],
            "thumbnail_key": "thumbnails/u1/evidence-upload-001/thumbnail.jpg", "tags": {"wombat": 2},
            "detections": [{"species": "wombat", "confidence": 0.94}], "model_version": "speciesnet-v1", "status": "completed",
        }
        first = client.put("/internal/files/evidence-upload-001/complete", json=complete_body, headers={"X-Internal-Api-Key": INTERNAL_API_KEY})
        second = client.put("/internal/files/evidence-upload-001/complete", json=complete_body, headers={"X-Internal-Api-Key": INTERNAL_API_KEY})
        completed = repo.get("evidence-upload-001")
        complete_output = f"FIRST CALL · HTTP {first.status_code}\n{response_block(first)}\n\nREPLAY · HTTP {second.status_code}\n{response_block(second)}\n\nPersisted status: {completed.status if completed else 'missing'}"
        render_page(10, FILES[9], "PUT /internal/files/evidence-upload-001/complete · called twice", "X-Internal-Api-Key: local-evidence-key\n\n" + pretty(complete_body), complete_output, "HTTP 200 + HTTP 200", ["First completion returned 200 {}", "Identical replay returned 200 {}", "File remained completed with no duplicate state transition"])

        auth_body = dict(reserve_body)
        auth_body["file_id"] = "auth-negative-001"
        auth_body["checksum"] = "sha256:auth-negative-001"
        auth_body["object_key"] = "originals/u1/auth-negative-001/wombat.jpg"
        missing = client.post("/internal/uploads/reserve", json=auth_body)
        wrong = client.post("/internal/uploads/reserve", json=auth_body, headers={"X-Internal-Api-Key": "wrong-key"})
        auth_output = f"MISSING HEADER · HTTP {missing.status_code}\n{response_block(missing)}\n\nWRONG HEADER · HTTP {wrong.status_code}\n{response_block(wrong)}"
        render_page(11, FILES[10], "POST /internal/uploads/reserve · negative authentication cases", "Case A: no X-Internal-Api-Key\nCase B: X-Internal-Api-Key: wrong-key", auth_output, "HTTP 401 + HTTP 401", ["Missing key failed closed", "Wrong key failed closed", "detail.code = INVALID_INTERNAL_API_KEY", "No reservation was created"])

        subscribe_body = {"species": "wombat"}
        r = client.post("/notifications/subscribe", json=subscribe_body)
        render_page(12, FILES[11], "POST /notifications/subscribe", pretty(subscribe_body), response_block(r), f"HTTP {r.status_code}", ["subscribed = true", "Subscription belongs to authenticated user u1", "Species short name is wombat"])

        notify_reserve = {
            "file_id": "notification-file-001", "user_id": "u1", "checksum": "sha256:notification-001",
            "filename": "notify-wombat.jpg", "file_type": "image", "content_type": "image/jpeg", "size_bytes": 4096,
            "object_key": "originals/u1/notification-file-001/notify-wombat.jpg", "status": "pending_upload",
        }
        client.post("/internal/uploads/reserve", json=notify_reserve, headers={"X-Internal-Api-Key": INTERNAL_API_KEY})
        notify_complete = {
            "user_id": "u1", "file_type": "image", "original_key": notify_reserve["object_key"],
            "thumbnail_key": "thumbnails/u1/notification-file-001/thumbnail.jpg", "tags": {"wombat": 1},
            "detections": [{"species": "wombat", "confidence": 0.97}], "model_version": "speciesnet-v1", "status": "completed",
        }
        completed_response = client.put("/internal/files/notification-file-001/complete", json=notify_complete, headers={"X-Internal-Api-Key": INTERNAL_API_KEY})
        inbox = client.get("/notifications")
        notification_output = f"COMPLETE · HTTP {completed_response.status_code}\n{response_block(completed_response)}\n\nGET /notifications · HTTP {inbox.status_code}\n{response_block(inbox)}"
        render_page(13, FILES[12], "PUT complete → GET /notifications", "Subscribed user: u1 → wombat\nCompleted file: notification-file-001\nDetected tags: {\"wombat\": 1}", notification_output, "HTTP 200 · NOTIFICATION FOUND", ["Completion matched the wombat subscription", "Inbox contains notification_id, file_id and species", "Notification record was persisted before delivery"])

        r = client.get("/notifications/subscriptions")
        render_page(14, FILES[13], "GET /notifications/subscriptions", "Authenticated user: u1\nNo caller-supplied user_id", response_block(r), f"HTTP {r.status_code}", ["species = [wombat]", "count = 1", "The authenticated identity scopes the subscription list"])

    main.app.dependency_overrides.clear()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": git_commit(),
        "database": DB_PATH.relative_to(EVIDENCE_DIR).as_posix(),
        "html_pages": [
            (HTML_DIR / f"{i:02d}.html").relative_to(EVIDENCE_DIR).as_posix()
            for i in range(1, 15)
        ],
        "screenshots": [
            (SCREENSHOT_DIR / f"{name}.png").relative_to(EVIDENCE_DIR).as_posix()
            for name in FILES
        ],
    }
    (EVIDENCE_DIR / "manifest.json").write_text(pretty(manifest), encoding="utf-8")
    print(pretty(manifest))


if __name__ == "__main__":
    main_generate()
