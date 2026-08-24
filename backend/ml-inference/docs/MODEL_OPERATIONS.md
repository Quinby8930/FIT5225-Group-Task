# Model Operations

## Required model files

Place the supplied files in `models/` locally or download them into the image
from a private OSS location during deployment:

- `model.pt`: fine-tuned SpeciesNet classifier;
- `mdv5a.pt`: MegaDetector file, retained for the team's media preprocessing
  pipeline;
- `config/labels.txt`: label mapping from the supplied archive.

The archive also mentions `onnx2torch`; it is included in the runtime because
the supplied `model.pt` imports it while loading the serialized model.
The current tested environment uses `onnx==1.22.0`, `onnx2torch==1.5.15`,
and `protobuf==7.36.0`; MegaDetector's legacy YOLOv5 package reports a
metadata-only protobuf upper-bound warning, but the supplied detector and
classifier were loaded and executed successfully.

The C service loads `model.pt`. B remains responsible for object detection,
cropping, and video frame extraction unless the team later changes the
boundary. The default C production mode uses the supplied `mdv5a.pt` to detect
animal boxes before classification. Set `ANIMAL_DETECTOR=full_image` only for
controlled experiments where B already sends a single cropped animal image.

## Model upgrade

To roll out a new model without changing B's source code:

1. Upload the new model to a versioned private OSS path.
2. Set `MODEL_PATH` and `MODEL_VERSION` in the deployment configuration.
3. Restart or redeploy the Function Compute/container revision.
4. Call `/ready` and `/infer` to verify the response reports the new version.

## Security rules

- Do not commit `.env`, OSS credentials, access keys, or signed URLs.
- Use a short-lived signed URL when AWS sends source media.
- Keep `INTERNAL_API_KEY` in an Alibaba Cloud environment secret and give the
  matching value to B through the team's secret-delivery process.
- Restrict outbound access and request sizes where the Function Compute or
  container platform supports it.
- Keep YOLOv5 and Matplotlib caches under `/tmp`; the service sets these paths
  automatically so it does not depend on a writable home directory.
