import { useEffect, useState } from "react";
import { uploadMedia } from "../../api/mediaApi";
import { isDuplicateFileError } from "../../api/apiClient";
import Field from "../../components/Field";

const STAGE_COPY = { hashing: "Calculating file checksum…", requesting: "Requesting a secure upload…", uploading: "Uploading file…", queued: "Upload complete; processing queued." };

export default function UploadPanel({ onStatus }) {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [stage, setStage] = useState("");
  const [receipt, setReceipt] = useState(null);

  useEffect(() => {
    if (!file) { setPreviewUrl(""); return undefined; }
    const objectUrl = URL.createObjectURL(file);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setReceipt(null);
    try {
      const result = await uploadMedia(file, { onStage: (nextStage) => setStage(nextStage) });
      setReceipt({ file_id: result.file_id, checksum: result.checksum, filename: file.name });
      onStatus({ type: "success", message: "Upload accepted. Processing has been queued." });
    } catch (error) {
      setStage("");
      onStatus({ type: "error", message: isDuplicateFileError(error) ? "Duplicate file detected by checksum." : error.message });
    }
  }

  return <section className="panel upload-panel"><div className="panel-title"><div><p className="eyebrow">New archive record</p><h2>Upload media</h2></div><span>Images and videos</span></div><form className="stack" onSubmit={submit}><Field label="File"><input type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime" onChange={(event) => { setFile(event.target.files?.[0] || null); setStage(""); }} /></Field>{file && <><div className="local-preview">{previewUrl && file.type.startsWith("image/") && <img src={previewUrl} alt={`Selected file ${file.name}`} />}{previewUrl && file.type.startsWith("video/") && <video controls preload="metadata" src={previewUrl} aria-label={`Selected video ${file.name}`}>Your browser cannot play this video.</video>}</div><dl className="metadata-grid"><div><dt>Filename</dt><dd>{file.name}</dd></div><div><dt>Type</dt><dd>{file.type || "Unknown"}</dd></div><div><dt>Size</dt><dd>{Math.ceil(file.size / 1024)} KB</dd></div></dl></>}<button type="submit" disabled={!file || Boolean(stage && stage !== "queued")}>{stage && stage !== "queued" ? "Uploading…" : "Upload"}</button></form>{stage && <p className="upload-stage" role="status">{STAGE_COPY[stage]}</p>}{receipt && <dl className="upload-receipt"><div><dt>Filename</dt><dd>{receipt.filename}</dd></div><div><dt>File ID</dt><dd>{receipt.file_id}</dd></div><div><dt>Checksum</dt><dd>{receipt.checksum?.slice(0, 12)}…</dd></div><div><dt>Status</dt><dd>Processing queued</dd></div></dl>}</section>;
}
