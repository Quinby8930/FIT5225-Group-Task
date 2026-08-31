# Real Model Test Evidence

Date: 31 August 2026

The supplied `model.pt`, `mdv5a.pt`, and `labels.txt` were loaded without
modifying the model weights and run against all 30 images in
`test_images.zip`. The production adapter now reproduces the course
`batch.py` preprocessing sequence: animal-only MegaDetector crops, 600 x 600
bilinear resize, source-format encode/reopen, and the classifier's 480 x 480
BHWC input.

| Metric | Result |
| --- | ---: |
| Images processed | 30 |
| Correct scientific top-1 labels | 28 |
| Incorrect scientific top-1 labels | 2 |
| Scientific top-1 accuracy | 93.33% |
| Images with a prediction | 30 |
| Raw prediction coverage | 100% |
| Lowest correct confidence | 0.471189 |
| Production species threshold | 0.45 |
| Model version | `provided-v1` |
| Detector mode | `megadetector` |

The two filename-label mismatches were:

- `Canis_familiaris_4.JPG`: expected `Canis_familiaris`, predicted
  `Canis_dingo` with confidence `0.994377`;
- `Felis_catus_4.JPG`: expected `Felis_catus`, predicted
  `Varanus_varius` with confidence `0.999973`.

This is a simple confusion pattern: each of the two pairs above occurs once;
the remaining 28 images are on the expected diagonal. At the website's short
tag level, both `Canis_familiaris` and `Canis_dingo` intentionally normalize
to `dingo`, so 29 of 30 images have the expected user-facing short tag.

The previous adapter skipped the course script's encode/reopen step and scored
27/30. A controlled preprocessing comparison showed that this omission caused
`Perameles_nasuta_3.JPG` to be classified as `Wallabia_bicolor`. The corrected
adapter scores 28/30 and matches the course `batch.py` top-1 class for all 30
images. Its mean absolute confidence difference from the reference run is
`0.000003639` and the maximum is `0.000074862`.

Raw accuracy was measured with the species threshold set to `0.0`. The
production threshold `0.45` is the largest 0.05 step below the lowest correct
confidence in this supplied set. Applying it retains all 30 official images;
it cannot remove the two remaining errors because both are high-confidence
model errors. This threshold is therefore only a rejection guard for uncertain
predictions, not a claim that the supplied model is error-free.

Detailed per-image results are recorded in `evidence/test_results.json`. This
is evidence for the ML component only. It does not establish why an existing
cloud database record has a particular tag and does not replace end-to-end AWS,
Alibaba Function Compute, database, or UI testing.
