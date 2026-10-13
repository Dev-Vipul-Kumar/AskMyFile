(function () {
  "use strict";

  const ITERS = (typeof PBKDF2_ITERS !== "undefined") ? PBKDF2_ITERS : 100000;

  const ACCEPT = {
    file:       [".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".txt",".csv",".rtf",".odt",".ods",".odp",".xml",".json",".md",".log",".epub",".pages",".numbers",".key"].join(","),
    folder:     ".zip,.rar,.7z,.tar,.gz,.bz2,.xz,.tgz,.iso",
    multimedia: [".jpg",".jpeg",".png",".gif",".webp",".svg",".bmp",".tiff",".heic",".avif",".mp4",".webm",".mov",".avi",".mkv",".flv",".wmv",".m4v",".3gp"].join(","),
    movie:      ".mp4,.mkv,.avi,.mov,.wmv,.flv,.m4v,.webm,.mpeg,.mpg,.3gp,.ts,.vob",
    text:       "",
  };

  const LABELS = {
    file:       { icon:"📄", title:"Drop a document or click to browse",       sub:"PDF, Word, Excel, PPT, TXT, CSV…" },
    folder:     { icon:"📦", title:"Drop a folder archive or click to browse",  sub:"ZIP, RAR, 7Z, TAR, GZ…" },
    multimedia: { icon:"🖼", title:"Drop an image or video or click to browse", sub:"JPG, PNG, GIF, MP4, WebM, MOV…" },
    movie:      { icon:"🎬", title:"Drop a movie file or click to browse",      sub:"MP4, MKV, AVI, MOV, WMV… (Subscription required)" },
    text:       { icon:"✏️", title:"Type your message below",                   sub:"Your text will be encrypted before sending" },
  };

  const dropZone        = document.getElementById("dropZone");
  const fileInput       = document.getElementById("fileInput");
  const textArea        = document.getElementById("textInput");
  const textAreaWrap    = document.getElementById("textInputWrap");
  const fileInfo        = document.getElementById("fileInfo");
  const fileNameEl      = document.getElementById("fileName");
  const fileSizeEl      = document.getElementById("fileSize");
  const dropIconEl      = document.getElementById("dropIcon");
  const dropTitleEl     = document.getElementById("dropTitle");
  const dropSubEl       = document.getElementById("dropSub");
  const uploadError     = document.getElementById("uploadError");
  const progressWrap    = document.getElementById("progressWrap");
  const progressLabel   = document.getElementById("progressLabel");
  const progressFill    = document.getElementById("progressFill");
  const createBtn       = document.getElementById("createShareBtn");
  const uploadArea      = document.getElementById("uploadArea");
  const resultArea      = document.getElementById("resultArea");
  const otpCodeEl       = document.getElementById("otpCode");
  const otpRevealEl     = document.getElementById("otpReveal");
  const emailSentMsgEl  = document.getElementById("emailSentMsg");
  const channelMsgEl    = document.getElementById("channelMsg");
  const shareAnotherBtn = document.getElementById("shareAnotherBtn");
  const guestOverlay    = document.getElementById("guestLimitOverlay");
  const overlayOtp      = document.getElementById("overlayOtp");

  let selectedFile = null;
  let selectedType = "file";

  function fmt(bytes) {
    if (bytes < 1024)       return bytes + " B";
    if (bytes < 1048576)    return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(2) + " MB";
    return (bytes / 1073741824).toFixed(2) + " GB";
  }

  function csrf() {
    if (typeof CSRF_TOKEN !== "undefined") return CSRF_TOKEN;
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }

  function showErr(msg) {
    if (!uploadError) return;
    uploadError.textContent = "⚠ " + msg;
    uploadError.classList.remove("hidden");
  }

  function clearErr() { uploadError && uploadError.classList.add("hidden"); }

  function setProgress(label) {
    if (!progressWrap) return;
    progressWrap.classList.remove("hidden");
    if (progressLabel) progressLabel.textContent = label;
  }

  function hideProgress() {
    progressWrap && progressWrap.classList.add("hidden");
    if (progressFill) { progressFill.classList.remove("indeterminate"); progressFill.style.width = "0%"; }
  }

  function checkReady() {
    if (!createBtn) return;
    if (selectedType === "text") {
      createBtn.disabled = !textArea || textArea.value.trim().length === 0;
    } else {
      createBtn.disabled = !selectedFile;
    }
  }

  function applyShareType(type) {
    selectedType  = type;
    selectedFile  = null;
    clearErr();
    checkReady();

    const lbl = LABELS[type] || LABELS.file;
    if (dropIconEl)  dropIconEl.textContent  = lbl.icon;
    if (dropTitleEl) dropTitleEl.textContent = lbl.title;
    if (dropSubEl)   dropSubEl.textContent   = lbl.sub;

    const hintEl = document.getElementById("shareTypeHint");
    const hints = {
      file:       "PDF, Word, Excel, PowerPoint, TXT, CSV, and more",
      folder:     "ZIP, RAR, 7Z, TAR, GZ — folder archives only",
      multimedia: "Images (JPG, PNG, GIF, WebP…) and Videos (MP4, MOV, WebM…)",
      movie:      "🎬 Subscription required · MP4, MKV, AVI, MOV, WMV…",
      text:       "Type any message — it will be encrypted before sending",
    };
    if (hintEl) {
      hintEl.textContent = hints[type] || "";
      hintEl.style.color = type === "movie" ? "var(--orange)" : "var(--text-dim)";
    }

    const isText = type === "text";

    if (dropZone) {
      dropZone.style.display = isText ? "none" : "";
      dropZone.style.borderColor = type === "movie" ? "rgba(245,158,11,0.6)" : "";
      dropZone.style.boxShadow   = type === "movie" ? "0 0 20px rgba(245,158,11,0.1)" : "";
    }
    if (textAreaWrap) textAreaWrap.style.display = isText ? "" : "none";
    if (fileInfo) fileInfo.classList.add("hidden");
    if (fileInput) { fileInput.value = ""; fileInput.accept = ACCEPT[type] || ""; }
  }

  document.querySelectorAll(".channel-pill[data-sharetype]").forEach(pill => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".channel-pill[data-sharetype]").forEach(p => {
        p.classList.remove("active"); p.setAttribute("aria-pressed", "false");
      });
      pill.classList.add("active");
      pill.setAttribute("aria-pressed", "true");
      applyShareType(pill.dataset.sharetype);
    });
  });

  if (textArea) {
    textArea.addEventListener("input", checkReady);
  }

  function handleFile(file) {
    if (!file) return;
    selectedFile = file;
    if (fileInfo)   fileInfo.classList.remove("hidden");
    if (fileNameEl) fileNameEl.textContent = file.name;
    if (fileSizeEl) fileSizeEl.textContent = fmt(file.size);
    clearErr();
    checkReady();
  }

  if (fileInput) fileInput.addEventListener("change", () => handleFile(fileInput.files[0]));

  if (dropZone) {
    dropZone.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput && fileInput.click(); }
    });
    dropZone.addEventListener("dragover",  e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
    dropZone.addEventListener("dragleave", ()  => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", e => {
      e.preventDefault(); dropZone.classList.remove("drag-over");
      const f = e.dataTransfer.files[0];
      if (f) {
        if (fileInput) { const dt = new DataTransfer(); dt.items.add(f); fileInput.files = dt.files; }
        handleFile(f);
      }
    });
  }

  const activePill = document.querySelector(".channel-pill.active[data-sharetype]");
  if (activePill) applyShareType(activePill.dataset.sharetype);
  else applyShareType("file");

  if (createBtn) {
    createBtn.addEventListener("click", async () => {
      clearErr();

      const shareType = selectedType;

      let fileToEncrypt;
      let displayName;

      if (shareType === "text") {
        const textContent = textArea ? textArea.value.trim() : "";
        if (!textContent) { showErr("Please enter some text to share."); return; }
        const textBytes = new TextEncoder().encode(textContent);
        fileToEncrypt = new File([textBytes], "message.txt", { type: "text/plain" });
        displayName   = "Text message";
      } else {
        if (!selectedFile) { showErr("Please select a file first."); return; }
        fileToEncrypt = selectedFile;
        displayName   = selectedFile.name;
      }

      createBtn.disabled = true;

      try {
        const otp     = Array.from(crypto.getRandomValues(new Uint8Array(6))).map(b => b % 10).join("");
        const otpHash = await AskMyCrypto.sha256hex(otp);

        setProgress("Encrypting…");
        if (progressFill) progressFill.classList.add("indeterminate");

        let enc;
        try {
          enc = await AskMyCrypto.encryptFile(fileToEncrypt, otp, ITERS);
        } catch (err) {
          hideProgress(); showErr("Encryption failed: " + err.message);
          createBtn.disabled = false; return;
        }

        const form = new FormData();
        form.append("file_data",         new Blob([enc.ciphertext], { type: "application/octet-stream" }), "payload.bin");
        form.append("salt_b64",          enc.saltB64);
        form.append("iv_b64",            enc.ivB64);
        form.append("algorithm_version", enc.algorithmVersion);
        form.append("original_filename", displayName);
        form.append("share_type",        shareType);
        form.append("otp_hash",          otpHash);

        setProgress("Uploading… 0%");
        if (progressFill) progressFill.classList.remove("indeterminate");

        let uploadResult;
        try {
          uploadResult = await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.upload.addEventListener("progress", e => {
              if (e.lengthComputable) {
                const pct = Math.round((e.loaded / e.total) * 100);
                if (progressLabel) progressLabel.textContent = `Uploading… ${pct}%`;
                if (progressFill)  progressFill.style.width = pct + "%";
              }
            });
            xhr.addEventListener("load", () => {
              try { resolve({ status: xhr.status, data: JSON.parse(xhr.responseText) }); }
              catch { resolve({ status: xhr.status, data: null }); }
            });
            xhr.addEventListener("error",   () => reject(new Error("Network error")));
            xhr.addEventListener("timeout",  () => reject(new Error("Upload timed out")));
            xhr.open("POST", "/shares/create/");
            xhr.setRequestHeader("X-CSRFToken", csrf());
            xhr.timeout = 300000;
            xhr.send(form);
          });
        } catch (uploadErr) {
          hideProgress(); showErr(uploadErr.message || "Upload failed.");
          createBtn.disabled = false; return;
        }

        hideProgress();
        const { status, data } = uploadResult;
        if (!data) { showErr("Unexpected server response."); createBtn.disabled = false; return; }

        if (status === 429 && data.error === "quota_exceeded") {
          showErr("Daily limit reached. Sign in or upgrade your plan.");
          createBtn.disabled = false; return;
        }

        if (!data.ok) {
          const MAP = {
            file_too_large:             "File exceeds the allowed size limit.",
            missing_file:               "No file received by server.",
            missing_crypto_params:      "Encryption params missing — please try again.",
            missing_otp_hash:           "OTP hash missing — please try again.",
            unverified_account:         "Please complete account verification first.",
            movie_requires_account:     "You must be signed in to share movies.",
            movie_requires_subscription:"🎬 Movie sharing requires a paid subscription.",
            movie_too_large:            `🎬 Movie exceeds your plan's limit (${data.limit_gb || "?"}GB).`,
          };
          showErr(MAP[data.error] || ("Error: " + (data.error || "unknown")));
          createBtn.disabled = false; return;
        }

        if (uploadArea)  uploadArea.classList.add("hidden");
        if (resultArea)  resultArea.classList.remove("hidden");

        const isRegistered = data.is_registered === true;

        if (isRegistered) {
          if (otpRevealEl)    otpRevealEl.style.display    = "none";
          if (emailSentMsgEl) emailSentMsgEl.style.display = "";
          if (channelMsgEl)   channelMsgEl.textContent     = "";
        } else {
          if (otpRevealEl)    otpRevealEl.style.display    = "";
          if (emailSentMsgEl) emailSentMsgEl.style.display = "none";
          if (otpCodeEl)      otpCodeEl.textContent        = otp;
          if (channelMsgEl)   channelMsgEl.textContent     = "👆 Share this 6-digit code with your recipient privately.";
        }

        if (data.channel === "email" && data.share_id) {
          fetch("/shares/send-otp-email/", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
            body: JSON.stringify({ otp, share_id: data.share_id }),
          }).catch(() => {});
        }

        if (data.guest_quota_hit) {
          if (overlayOtp) overlayOtp.textContent = otp;
          if (guestOverlay) {
            guestOverlay.classList.remove("hidden");
            const first = guestOverlay.querySelector("a,button,input");
            if (first) first.focus();
          }
        }

      } catch (err) {
        hideProgress(); showErr("Unexpected error: " + err.message);
        createBtn.disabled = false;
      }
    });
  }

  if (shareAnotherBtn) {
    shareAnotherBtn.addEventListener("click", () => {
      selectedFile = null;
      if (uploadArea)  uploadArea.classList.remove("hidden");
      if (resultArea)  resultArea.classList.add("hidden");
      if (fileInfo)    fileInfo.classList.add("hidden");
      if (fileInput)   fileInput.value = "";
      if (textArea)    textArea.value  = "";
      clearErr();
      if (createBtn) createBtn.disabled = true;
      const first = document.querySelector(".channel-pill[data-sharetype]");
      if (first) first.click();
    });
  }

  if (guestOverlay) {
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !guestOverlay.classList.contains("hidden")) {
        e.preventDefault(); e.stopImmediatePropagation();
      }
    });
    guestOverlay.addEventListener("click", e => {
      if (e.target === guestOverlay) {
        e.stopPropagation();
        (guestOverlay.querySelector("a,button") || guestOverlay).focus();
      }
    });
    guestOverlay.addEventListener("keydown", e => {
      if (e.key !== "Tab") return;
      const els = [...guestOverlay.querySelectorAll('a[href],button:not([disabled]),input,[tabindex]:not([tabindex="-1"])')];
      if (!els.length) return;
      if (e.shiftKey && document.activeElement === els[0]) { e.preventDefault(); els[els.length-1].focus(); }
      else if (!e.shiftKey && document.activeElement === els[els.length-1]) { e.preventDefault(); els[0].focus(); }
    });
  }

})();
