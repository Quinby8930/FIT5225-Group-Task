# Real-model evidence

`test_results.json` is generated locally from the supplied `test_images.zip`
using the supplied `model.pt` and `mdv5a.pt`. It is reproducible with:

```bash
MPLCONFIGDIR=/tmp/pacific-bioarchive-mpl \
YOLOV5_CONFIG_DIR=/tmp/pacific-bioarchive-yolo \
.venv/bin/python scripts/run_test_images.py \
  --images-zip "$HOME/Downloads/test_images.zip"
```

The JSON is test evidence for the member C ML module, not a substitute for
the team's end-to-end AWS upload, database, and UI tests.

The summarized result is documented in `docs/REAL_MODEL_TEST.md`.
