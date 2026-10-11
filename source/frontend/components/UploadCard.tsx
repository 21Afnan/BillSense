"use client";

import { useEffect, useRef, useState } from "react";

type Props = { onExtract: (file: File) => Promise<void>; busy: boolean; error: string | null; hasResult: boolean };

export default function UploadCard({ onExtract, busy, error, hasResult }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const choose = (candidate?: File) => {
    if (!candidate) return;
    if (!/^image\/(png|jpeg|webp)$/.test(candidate.type)) { setLocalError("Please choose a PNG, JPG, or WEBP image."); return; }
    if (candidate.size > 15 * 1024 * 1024) { setLocalError("This image is larger than 15 MB."); return; }
    setLocalError(null);
    setFile(candidate);
    setPreview((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(candidate); });
  };

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  return (
    <div className="card upload-card">
      <div className="section-kicker">Step 1</div><h2>Upload your bill</h2>
      <p className="section-copy">Use a clear scan, screenshot, or photo. Personal information stays out of the analysis.</p>
      <div className={`drop ${drag ? "drag" : ""}`} onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); choose(e.dataTransfer.files[0]); }}>
        <input ref={input} type="file" accept="image/*,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif" hidden onChange={(e) => choose(e.target.files?.[0])} />
        {file ? <div className="file-selected"><span className="file-icon">✓</span><div><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(2)} MB · Ready to analyze</small></div></div> :
          <div className="drop-content"><span className="upload-icon">↑</span><strong>Drop your bill here</strong><span>or click to browse your device</span><small>PNG, JPG, or WEBP · Maximum 15 MB</small></div>}
      </div>
      {preview && <div className="preview-wrap"><img src={preview} alt="Selected electricity bill preview" className="preview" /><button className="change-file" onClick={() => input.current?.click()} disabled={busy}>Change</button></div>}
      <button className="primary full" disabled={!file || busy} onClick={() => file && onExtract(file)}>
        {busy ? <><span className="button-spinner" /> Analyzing bill…</> : <>{hasResult ? "Analyze again" : "Analyze my bill"} <span>→</span></>}
      </button>
      {(localError || error) && <p className="error"><b>Couldn’t analyze this bill.</b><br />{localError || error}</p>}
      <div className="provider-list"><span>Supported</span><b>KE</b><b>LESCO</b><b>IESCO</b></div>
    </div>
  );
}
