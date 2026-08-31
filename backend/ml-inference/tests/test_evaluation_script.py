from scripts.run_test_images import summarize_results


def test_summary_reports_accuracy_errors_coverage_and_confusion() -> None:
    results = [
        {
            "file": "test_images/Felis_catus_1.JPG",
            "expected_label": "Felis_catus",
            "predicted_label": "Felis_catus",
            "confidence": 0.91,
            "match": True,
        },
        {
            "file": "test_images/Mus_musculus_1.JPG",
            "expected_label": "Mus_musculus",
            "predicted_label": "Felis_catus",
            "confidence": 0.62,
            "match": False,
        },
        {
            "file": "test_images/Rattus_1.JPG",
            "expected_label": "Rattus",
            "predicted_label": None,
            "confidence": None,
            "match": False,
        },
    ]

    assert summarize_results(results) == {
        "image_count": 3,
        "correct_count": 1,
        "error_count": 2,
        "top1_accuracy": 0.3333,
        "prediction_count": 2,
        "prediction_coverage": 0.6667,
        "minimum_correct_confidence": 0.91,
        "recommended_species_confidence_threshold": 0.9,
        "errors": [
            {
                "file": "test_images/Mus_musculus_1.JPG",
                "expected_label": "Mus_musculus",
                "predicted_label": "Felis_catus",
                "confidence": 0.62,
            },
            {
                "file": "test_images/Rattus_1.JPG",
                "expected_label": "Rattus",
                "predicted_label": None,
                "confidence": None,
            },
        ],
        "confusion": [
            {
                "expected_label": "Mus_musculus",
                "predicted_label": "Felis_catus",
                "count": 1,
            },
            {
                "expected_label": "Rattus",
                "predicted_label": None,
                "count": 1,
            },
        ],
    }
