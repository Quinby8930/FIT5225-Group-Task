import { useCallback, useEffect, useRef, useState } from "react";
import { getUploadStatus, uploadMedia } from "../../api/mediaApi";
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
import {
  loadRecentUploads,
  mergeUploadStatus,
  pendingUploadIds,
  rememberRecentUpload,
  saveRecentUploads,
  uploadStatusView,
} from "../../lib/recentUploads.mjs";

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

function sessionStorageOrNull() {
  try {
    return globalThis.sessionStorage || null;
  } catch {
    return null;
  }
}

function processingMessage(stage, upload) {
  if (stage !== "queued" || !upload) return STAGE_COPY[stage];
  if (upload.status === "processing") return "Upload complete; AI processing is running.";
  if (upload.status === "completed") return "Processing complete; the archive record is ready.";
  if (upload.status === "failed") return "Upload succeeded, but processing failed.";
  return STAGE_COPY.queued;
}

function RecentUploadCard({ upload, onExploreSpecies }) {
  const view = uploadStatusView(upload);
  const species = Object.keys(upload.tags || {});
  return (
    <article className="recent-upload-card">
      <div className="recent-upload-heading">
        <div>
          <strong>{upload.filename}</strong>
          <code>{upload.file_id}</code>
        </div>
        <span className={`upload-status status-${upload.status}`}>{view.label}</span>
      </div>
      {view.tagRows.length > 0 && (
        <div className="recent-upload-details">
          <h3>Archive tags</h3>
          <ul>{view.tagRows.map((row) => <li key={row}>{row}</li>)}</ul>
        </div>
      )}
      {view.detectionRows.length > 0 && (
        <div className="recent-upload-details">
          <h3>Original AI detections</h3>
          <ul>{view.detectionRows.map((row) => <li key={row}>{row}</li>)}</ul>
          {view.detectionNote && (
            <p className="ai-result-notice">{view.detectionNote}</p>
          )}
        </div>
      )}
      {view.modelVersion && (
        <p className="recent-upload-model"><strong>Model version</strong> {view.modelVersion}</p>
      )}
      {view.failure && <p className="recent-upload-failure" role="alert">{view.failure}</p>}
      {species.length > 0 && (
        <div className="btn-row">
          {species.map((label) => (
            <button
              key={label}
              type="button"
              className="btn btn-secondary"
              onClick={() => onExploreSpecies(label)}
            >
              Explore {label}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

export default function UploadPanel({
  active,
  getActiveSession,
  onStatus,
  onNavigate,
  onExploreSpecies,
  sessionKey,
  userSubject,
}) {
  const [uploadState, setUploadState] = useState({
    file: null,
    stage: "",
    receipt: null,
    duplicate: null,
    submitting: false,
  });
  const [recentUploads, setRecentUploads] = useState(() => (
    loadRecentUploads(sessionStorageOrNull(), userSubject)
  ));
  const [previewUrl, setPreviewUrl] = useState("");
  const mountedRef = useRef(true);
  const previewMediaRef = useRef(null);
  const uploadRunRef = useRef(0);
  const pollInFlightRef = useRef(false);
  const { file, stage, receipt, duplicate } = uploadState;
  const duplicateCard = duplicate ? duplicateCardModel(duplicate, file?.type) : null;
  const receiptUpload = recentUploads.find((item) => item.file_id === receipt?.file_id);
  const activeUploadIds = pendingUploadIds(recentUploads);
  const activeUploadIdsKey = activeUploadIds.join("|");

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
    saveRecentUploads(sessionStorageOrNull(), userSubject, recentUploads);
  }, [recentUploads, userSubject]);

  useEffect(() => {
    if (!file) {
      setPreviewUrl("");
      return undefined;
    }
    const objectUrl = URL.createObjectURL(file);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  const refreshUploadStatuses = useCallback(async (fileIds, announce = false) => {
    if (!fileIds.length || pollInFlightRef.current) return;
    const sourceSession = sessionKey;
    pollInFlightRef.current = true;
    try {
      const results = await Promise.allSettled(fileIds.map((fileId) => getUploadStatus(fileId)));
      if (!mountedRef.current || getActiveSession?.() !== sourceSession) return;
      const fulfilled = results
        .filter((result) => result.status === "fulfilled")
        .map((result) => result.value);
      if (fulfilled.length) {
        setRecentUploads((current) => fulfilled.reduce(
          (uploads, status) => mergeUploadStatus(uploads, status),
          current,
        ));
      }
      if (announce) {
        const failures = results.length - fulfilled.length;
        onStatus(failures
          ? { type: "error", message: `${failures} upload status update(s) could not be loaded.` }
          : { type: "success", message: "Recent upload status refreshed." });
      }
    } finally {
      pollInFlightRef.current = false;
    }
  }, [getActiveSession, onStatus, sessionKey]);

  useEffect(() => {
    if (!active || !activeUploadIdsKey) return undefined;
    const ids = activeUploadIdsKey.split("|");
    void refreshUploadStatuses(ids);
    const intervalId = globalThis.setInterval(() => {
      void refreshUploadStatuses(ids);
    }, 5000);
    return () => globalThis.clearInterval(intervalId);
  }, [active, activeUploadIdsKey, refreshUploadStatuses]);

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
      const uploadReceipt = {
        file_id: result.file_id,
        checksum: result.checksum,
        filename: file.name,
        file_type: file.type.startsWith("video/") ? "video" : "image",
        status: "pending_upload",
        upload_time: new Date().toISOString(),
      };
      setUploadState((current) => completeUpload(current, uploadReceipt));
      setRecentUploads((current) => rememberRecentUpload(current, uploadReceipt));
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
          <p className="upload-stage" role="status">{processingMessage(stage, receiptUpload)}</p>
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
          <p className="receipt-guidance">
            Processing status updates automatically. This upload remains in Recent uploads if
            you refresh this page.
          </p>
          <div className="btn-row">
            <button type="button" className="btn btn-secondary" onClick={() => onNavigate("explore")}>Go to Explore</button>
            <button type="button" className="btn btn-quiet" onClick={() => onNavigate("notifications")}>Open Notifications</button>
          </div>
        </>
      )}
      {recentUploads.length > 0 && (
        <section className="recent-uploads" aria-labelledby="recent-uploads-heading">
          <div className="recent-uploads-title">
            <div>
              <p className="eyebrow">This browser session</p>
              <h2 id="recent-uploads-heading">Recent uploads</h2>
            </div>
            <button
              type="button"
              className="btn btn-quiet"
              disabled={!activeUploadIds.length || pollInFlightRef.current}
              onClick={() => refreshUploadStatuses(activeUploadIds, true)}
            >
              Refresh status
            </button>
          </div>
          <p className="recent-uploads-guidance">
            Pending records are checked every five seconds while this page is open.
          </p>
          <div className="recent-upload-list">
            {recentUploads.map((upload) => (
              <RecentUploadCard
                key={upload.file_id}
                upload={upload}
                onExploreSpecies={onExploreSpecies}
              />
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
