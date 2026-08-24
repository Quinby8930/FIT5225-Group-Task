#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

from app.config import Settings
from app.main import create_server


def main() -> None:
    os.environ.setdefault("INFERENCE_BACKEND", "mock")
    os.environ.setdefault("INTERNAL_API_KEY", "local-demo-secret")
    server = create_server(Settings.from_env(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        payload = json.dumps(
            {
                "file_id": "contract-check",
                "media_type": "image",
                "image_urls": ["https://example.invalid/image.jpg"],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://{host}:{port}/infer",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Api-Key": "local-demo-secret",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc:
            if exc.code != 422:
                raise
            print("Contract check passed: authenticated invalid source returned 422")
    finally:
        server.shutdown()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
