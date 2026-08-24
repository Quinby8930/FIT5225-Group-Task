# Real Model Test Evidence

Date: 24 August 2026

The supplied `model.pt`, `mdv5a.pt`, and `labels.txt` were loaded locally and
run against all 30 images in `test_images.zip`.

| Metric | Result |
| --- | ---: |
| Images processed | 30 |
| Top-1 filename-label matches | 27 |
| Top-1 accuracy | 90% |
| Total CPU/MPS runtime | 27.36 seconds |
| Model version | `provided-v1` |
| Detector mode | `megadetector` |

The three filename-label mismatches were:

- `Canis_familiaris_4.JPG` predicted `Canis_dingo`;
- `Felis_catus_4.JPG` predicted `Varanus_varius`;
- `Perameles_nasuta_3.JPG` predicted `Wallabia_bicolor`.

The result is recorded in `evidence/test_results.json`. The service does not
hide low-confidence or incorrect predictions; it returns the predicted tags,
per-detection confidence, and model version to the caller.

This is evidence for C's ML component only. It does not replace the team's
end-to-end test of AWS upload, D's database insertion, or the final UI.

