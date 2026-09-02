import { useEffect, useRef, useState } from "react";
import { uploadMedia } from "../../api/mediaApi";
import Field from "../../components/Field";
import FilePicker from "../../components/FilePicker";
import {
  copyDuplicateFileId,
  duplicateCardModel,
  duplicateFailureForError,
} from "../../lib/duplicateUpload.mjs";
import {
  canCommitUploadEffect,
  canSubmitUpload,
  completeUpload,
  failUpload,
  pauseInactivePreview,
  selectUploadFile,
  startUpload,
} from "../../lib/uploadWorkflow.mjs";

const STAGE_COPY = {
  hashing: "Calculating file checksum…",
  requesting: "Requesting a secure upload…",
  uploading: "Uploading file…",
  queued: "Upload complete; processing queued.",
};
const STAGES = [
  ["hashing", "Checksum"],
  ["requesting", "Request"],
  ["uploading", "Uploading"],
  ["queued", "Queued"],
];

function stepClass(stepKey, currentStage) {
  const order = STAGES.map(([key]) => key);
  const currentIndex = order.indexOf(currentStage);
  const stepIndex = order.indexOf(stepKey);
  if (stepIndex < currentIndex) return "done";
  if (stepIndex === currentIndex) return "current";
  return "todo";
}

export default function UploadPanel({
  active,
  getActiveSession,
  onStatus,
  onNavigate,
  onExploreSpecies,
  sessionKey,
}) {
  const [uploadState, setUploadState] = useState({
    file: null,
    stage: "",
    receipt: null,
    duplicate: null,
    submitting: false,
  });
  const [previewUrl, setPreviewUrl] = useState("");
  const mountedRef = useRef(true);
  const previewMediaRef = useRef(null);
  const uploadRunRef = useRef(0);
  const { file, stage, receipt, duplicate } = uploadState;
  const duplicateCard = duplicate ? duplicateCardModel(duplicate, file?.type) : null;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      uploadRunRef.current += 1;
      pauseInactivePreview(false, previewMediaRef.current);
    };
  }, []);

  useEffect(() => {
    pauseInactivePreview(active, previewMediaRef.current);
  }, [active]);

  useEffect(() => {
    if (!file) {
      setPreviewUrl("");
      return undefined;
    }
    const objectUrl = URL.createObjectURL(file);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  async function submit(event) {
    event.preventDefault();
    if (!canSubmitUpload(uploadState)) return;
    const sourceRun = uploadRunRef.current + 1;
    uploadRunRef.current = sourceRun;
    const canCommit = () => canCommitUploadEffect({
      mounted: mountedRef.current,
      activeSession: getActiveSession?.(),
      sourceSession: sessionKey,
      currentRun: uploadRunRef.current,
      sourceRun,
    });
    setUploadState((current) => startUpload(current));
    try {
      const result = await uploadMedia(file, {
        onStage: (nextStage) => {
          if (canCommit()) setUploadState((current) => ({ ...current, stage: nextStage }));
        },
      });
      if (!canCommit()) return;
      setUploadState((current) => completeUpload(current, { file_id: result.file_id, checksum: result.checksum, filename: file.name }));
      onStatus({ type: "success", message: "Upload accepted. Processing has been queued." });
    } catch (error) {
      if (!canCommit()) return;
      const failure = duplicateFailureForError(error);
      setUploadState((current) => failUpload(current, failure.duplicate));
      onStatus(failure.duplicate ? null : {
        type: "error",
        message: failure.message,
      });
    }
  }

  return (
    <section className="panel upload-panel">
      <div className="panel-title">
        <div>
          <p className="eyebrow">New archive record</p>
          <h1>Upload</h1>
        </div>
        <span>Images and videos</span>
      </div>
      <div className={file || receipt ? "upload-entry-grid single" : "upload-entry-grid"}>
      <form className="stack" onSubmit={submit}>
        <Field label="File">
          <FilePicker
            accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime"
            ariaLabel="Choose file to upload"
            disabled={uploadState.submitting}
            variant="dropzone"
            onChange={(nextFile) => {
              onStatus(null);
              setUploadState((current) => selectUploadFile(current, nextFile));
            }}
          />
        </Field>
        {file && (
          <>
            <div className="local-preview">
              {previewUrl && file.type.startsWith("image/") && <img ref={previewMediaRef} src={previewUrl} alt={`Selected file ${file.name}`} />}
              {previewUrl && file.type.startsWith("video/") && (
                <video ref={previewMediaRef} controls preload="metadata" src={previewUrl} aria-label={`Selected video ${file.name}`}>
                  Your browser cannot play this video.
                </video>
              )}
            </div>
            <dl className="metadata-grid">
              <div><dt>Filename</dt><dd>{file.name}</dd></div>
              <div><dt>Type</dt><dd>{file.type || "Unknown"}</dd></div>
              <div><dt>Size</dt><dd>{Math.ceil(file.size / 1024)} KB</dd></div>
            </dl>
          </>
        )}
        <div>
          <button type="submit" className="btn btn-primary" disabled={!canSubmitUpload(uploadState)}>
            {uploadState.submitting ? "Uploading…" : "Upload"}
          </button>
        </div>
      </form>
      {!file && !receipt && (
        <aside className="upload-guide" aria-labelledby="upload-guide-heading">
          <p className="eyebrow">Before you upload</p>
          <h2 id="upload-guide-heading">One file, automated from intake to search</h2>
          <ol>
            <li><span>01</span><div><strong>Choose media</strong><small>JPEG, PNG, WebP, MP4, or MOV.</small></div></li>
            <li><span>02</span><div><strong>Private transfer</strong><small>A checksum prevents duplicate uploads before storage.</small></div></li>
            <li><span>03</span><div><strong>Cloud processing</strong><small>Thumbnails, video frames, species tags, and metadata are created automatically.</small></div></li>
          </ol>
          <p>Once processing completes, find the record from Explore.</p>
        </aside>
      )}
      </div>
      {stage && (
        <>
          <ol className="upload-steps" aria-label="Upload progress">
            {STAGES.map(([key, label]) => (
              <li key={key} className={stepClass(key, stage)} aria-current={stepClass(key, stage) === "current" ? "step" : undefined}>
                {label}
              </li>
            ))}
          </ol>
          <p className="upload-stage" role="status">{STAGE_COPY[stage]}</p>
        </>
      )}
      {duplicateCard && (
        <aside className="duplicate-upload-card" aria-labelledby="duplicate-upload-heading">
          <p className="eyebrow">Duplicate prevented</p>
          <h2 id="duplicate-upload-heading">{duplicateCard.heading}</h2>
          <p>{duplicateCard.guidance}</p>
          <dl>
            <div>
              <dt>Existing File ID</dt>
              <dd><code>{duplicateCard.fileId}</code></dd>
            </div>
          </dl>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => copyDuplicateFileId(
              duplicateCard.fileId,
              navigator.clipboard,
            ).then(
              () => onStatus({ type: "success", message: "File ID copied." }),
              () => onStatus({ type: "error", message: "File ID could not be copied." }),
            )}
          >
            Copy File ID
          </button>
          {duplicateCard.tagRows.length > 0 && (
            <div className="duplicate-tags">
              <h3>Current database tags</h3>
              <ul>
                {duplicateCard.tagRows.map(({ species, label }) => (
                  <li key={species}>{label}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="btn-row">
            {duplicateCard.exploreActions.map((species) => (
              <button
                key={species}
                type="button"
                className="btn btn-secondary"
                onClick={() => onExploreSpecies(species)}
              >
                Explore {species}
              </button>
            ))}
            {duplicateCard.emptyTags && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => onNavigate("explore")}
              >
                Go to Explore
              </button>
            )}
          </div>
        </aside>
      )}
      {receipt && (
        <>
          <dl className="upload-receipt">
            <div><dt>Filename</dt><dd>{receipt.filename}</dd></div>
            <div><dt>File ID</dt><dd>{receipt.file_id}</dd></div>
            <div><dt>Checksum</dt><dd>{receipt.checksum?.slice(0, 12)}…</dd></div>
            <div><dt>Status</dt><dd>Processing queued</dd></div>
          </dl>
          <p className="receipt-guidance">
            Processing runs automatically. Find the record in Explore once it completes, or
            subscribe to a species in Notifications.
          </p>
          <div className="btn-row">
            <button type="button" className="btn btn-secondary" onClick={() => onNavigate("explore")}>Go to Explore</button>
            <button type="button" className="btn btn-quiet" onClick={() => onNavigate("notifications")}>Open Notifications</button>
          </div>
        </>
      )}
    </section>
  );
}
