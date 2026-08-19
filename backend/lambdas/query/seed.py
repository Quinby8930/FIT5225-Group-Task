"""Seed the local SQLite database with realistic demo records.

Tag keys use the **team short species name** (last word of the labels.txt
common name) — see `app/species.py` and INTEGRATION.md. Storage locations are
S3 **keys**, not URLs.

Run from the project root:  python seed.py
"""

from app.repository import SQLiteRepository
from app.schemas import FileRecord

# (file_id, user_id, type, object_key, thumbnail_key, tags, checksum)
_SAMPLES = [
    ("f1", "u1", "image", "originals/u1/a1.jpg", "thumbnails/u1/a1.jpg",
     {"dingo": 2, "wombat": 1}, "sha256:aa"),
    ("f2", "u1", "image", "originals/u1/a2.jpg", "thumbnails/u1/a2.jpg",
     {"wombat": 2, "magpie": 1}, "sha256:bb"),
    ("f3", "u2", "image", "originals/u2/a3.jpg", "thumbnails/u2/a3.jpg",
     {"kangaroo": 3}, "sha256:cc"),
    ("f4", "u2", "image", "originals/u2/a4.jpg", "thumbnails/u2/a4.jpg",
     {"dingo": 1, "magpie": 4}, "sha256:dd"),
    ("f5", "u1", "video", "originals/u1/v1.mp4", None,
     {"dingo": 1, "wombat": 3}, "sha256:ee"),
    ("f6", "u3", "image", "originals/u3/a5.jpg", "thumbnails/u3/a5.jpg",
     {"magpie": 1}, "sha256:ff"),
    ("f7", "u3", "video", "originals/u3/v2.mp4", None,
     {"fox": 2, "dingo": 2}, "sha256:gg"),
]


def main() -> None:
    repo = SQLiteRepository("data/pacific_bioarchive.db")
    for fid, uid, ftype, obj_key, thumb_key, tags, checksum in _SAMPLES:
        repo.add(FileRecord(
            file_id=fid, user_id=uid, file_type=ftype,
            object_key=obj_key, thumbnail_key=thumb_key,
            tags=tags, checksum=checksum, status="completed",
        ))
    print(f"Seeded {len(_SAMPLES)} records.")


if __name__ == "__main__":
    main()
