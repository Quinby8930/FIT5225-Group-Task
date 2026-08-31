# Real-model evidence

`test_results.json` was generated locally from the supplied `test_images.zip`
using the supplied, unmodified `model.pt` and `mdv5a.pt`. It is reproducible
from the ML service directory with:

```bash
python scripts/run_test_images.py \
  --images-zip /path/to/test_images.zip \
  --model /path/to/model.pt \
  --detector /path/to/mdv5a.pt \
  --labels config/labels.txt \
  --detection-threshold 0.05 \
  --species-confidence-threshold 0.0 \
  --output evidence/test_results.json
```

The `0.0` species threshold records raw top-1 accuracy and confidence. The
deployed service uses the separately configured `0.45` species threshold; the
evidence summary records why that value was selected from this supplied test
set.

The JSON is test evidence for the ML module, not a substitute for the team's
end-to-end cloud upload, database, and UI tests. The summarized result and its
limitations are documented in `docs/REAL_MODEL_TEST.md`.
