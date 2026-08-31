#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from app.backends.speciesnet import SpeciesNetBackend
from app.inference import InferenceService
from app.schemas import parse_inference_request


def expected_label(name: str) -> str:
    return Path(name).stem.rsplit("_", 1)[0]


def summarize_results(results: list[dict[str, object]]) -> dict[str, object]:
    correct_count = sum(bool(item["match"]) for item in results)
    prediction_count = sum(item["predicted_label"] is not None for item in results)
    error_items = [item for item in results if not item["match"]]
    errors = [
        {
            "file": item["file"],
            "expected_label": item["expected_label"],
            "predicted_label": item["predicted_label"],
            "confidence": item["confidence"],
        }
        for item in error_items
    ]
    confusion_counts = Counter(
        (item["expected_label"], item["predicted_label"]) for item in error_items
    )
    confusion = [
        {
            "expected_label": expected,
            "predicted_label": predicted,
            "count": count,
        }
        for (expected, predicted), count in sorted(
            confusion_counts.items(),
            key=lambda entry: (str(entry[0][0]), str(entry[0][1])),
        )
    ]
    image_count = len(results)
    correct_confidences = [
        float(item["confidence"])
        for item in results
        if item["match"] and item["confidence"] is not None
    ]
    minimum_correct_confidence = (
        min(correct_confidences) if correct_confidences else None
    )
    recommended_threshold = (
        round(
            math.floor((minimum_correct_confidence + 1e-12) / 0.05) * 0.05,
            2,
        )
        if minimum_correct_confidence is not None
        else 0.0
    )
    return {
        "image_count": image_count,
        "correct_count": correct_count,
        "error_count": image_count - correct_count,
        "top1_accuracy": round(correct_count / image_count, 4) if image_count else 0.0,
        "prediction_count": prediction_count,
        "prediction_coverage": (
            round(prediction_count / image_count, 4) if image_count else 0.0
        ),
        "minimum_correct_confidence": minimum_correct_confidence,
        "recommended_species_confidence_threshold": recommended_threshold,
        "errors": errors,
        "confusion": confusion,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the supplied test image set")
    parser.add_argument("--images-zip", required=True, type=Path)
    parser.add_argument("--model", default="models/model.pt", type=Path)
    parser.add_argument("--detector", default="models/mdv5a.pt", type=Path)
    parser.add_argument("--labels", default="config/labels.txt", type=Path)
    parser.add_argument("--output", default="evidence/test_results.json", type=Path)
    parser.add_argument(
        "--detection-threshold",
        type=float,
        default=float(os.getenv("DETECTION_THRESHOLD", "0.05")),
    )
    parser.add_argument(
        "--species-confidence-threshold",
        type=float,
        default=float(os.getenv("SPECIES_CONFIDENCE_THRESHOLD", "0.0")),
        help="Use 0.0 to measure raw top-1 accuracy before production rejection.",
    )
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
        detection_threshold=args.detection_threshold,
        species_confidence_threshold=args.species_confidence_threshold,
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
            result = InferenceService(
                backend, lambda _, *, deadline=None: raw
            ).infer(request)
            elapsed_ms = round((time.perf_counter() - item_started) * 1000, 2)
            top_detection = max(
                result.detections,
                key=lambda detection: float(detection["confidence"]),
                default=None,
            )
            predicted = top_detection["species"] if top_detection else None
            confidence = top_detection["confidence"] if top_detection else None
            expected = expected_label(image_name)
            results.append(
                {
                    "file": image_name,
                    "expected_label": expected,
                    "predicted_label": predicted,
                    "confidence": confidence,
                    "match": predicted == expected,
                    "tags": result.tags,
                    "detections": result.detections,
                    "elapsed_ms": elapsed_ms,
                }
            )

    summary = summarize_results(results)
    output = {
        "model_version": backend.model_version,
        "detector_mode": backend.detector_mode,
        "detection_threshold": args.detection_threshold,
        "species_confidence_threshold": args.species_confidence_threshold,
        **summary,
        "top1_matches": summary["correct_count"],
        "total_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in output if key != "results"}, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
