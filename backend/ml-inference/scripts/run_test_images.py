#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from zipfile import ZipFile

from app.backends.speciesnet import SpeciesNetBackend
from app.inference import InferenceService
from app.schemas import parse_inference_request


def expected_label(name: str) -> str:
    return Path(name).stem.rsplit("_", 1)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the supplied test image set")
    parser.add_argument("--images-zip", required=True, type=Path)
    parser.add_argument("--model", default="models/model.pt", type=Path)
    parser.add_argument("--detector", default="models/mdv5a.pt", type=Path)
    parser.add_argument("--labels", default="config/labels.txt", type=Path)
    parser.add_argument("--output", default="evidence/test_results.json", type=Path)
    args = parser.parse_args()

    os.environ.setdefault("MPLCONFIGDIR", "/tmp/pacific-bioarchive-mpl")
    os.environ.setdefault("YOLOV5_CONFIG_DIR", "/tmp/pacific-bioarchive-yolo")
    Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["YOLOV5_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)

    backend = SpeciesNetBackend(
        model_path=args.model,
        detector_model_path=args.detector,
        labels_path=args.labels,
        model_version=os.getenv("MODEL_VERSION", "provided-v1"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.05")),
    )

    results: list[dict[str, object]] = []
    started = time.perf_counter()
    with ZipFile(args.images_zip) as archive:
        image_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("test_images/") and name.lower().endswith((".jpg", ".jpeg", ".png"))
        )
        for image_name in image_names:
            raw = archive.read(image_name)
            request = parse_inference_request(
                {
                    "file_id": Path(image_name).stem,
                    "media_type": "image",
                    "image_urls": ["https://local.test/image.jpg"],
                },
                max_source_urls=60,
            )
            item_started = time.perf_counter()
            result = InferenceService(backend, lambda _: raw).infer(request)
            elapsed_ms = round((time.perf_counter() - item_started) * 1000, 2)
            predicted = next(iter(result.tags), None)
            expected = expected_label(image_name)
            results.append(
                {
                    "file": image_name,
                    "expected_label": expected,
                    "predicted_label": predicted,
                    "match": predicted == expected,
                    "tags": result.tags,
                    "detections": result.detections,
                    "elapsed_ms": elapsed_ms,
                }
            )

    matches = sum(bool(item["match"]) for item in results)
    output = {
        "model_version": backend.model_version,
        "detector_mode": backend.detector_mode,
        "image_count": len(results),
        "top1_matches": matches,
        "top1_accuracy": round(matches / len(results), 4) if results else 0.0,
        "total_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in output if key != "results"}, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
