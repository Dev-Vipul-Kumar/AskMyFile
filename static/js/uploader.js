/**
 * AskMyFile – OTP-based Upload Flow
 *
 * Share types and their file-picker filters:
 *   file        → Documents: PDF, Word, Excel, PowerPoint, TXT, CSV, etc.
 *   folder      → Archives only: ZIP, RAR, 7Z, TAR, GZ, etc.
 *   multimedia  → Images + Videos: JPG, PNG, GIF, MP4, AVI, etc.
 *   movie       → Movie files: MP4, MKV, AVI, MOV, etc. (subscription only)
 */
(function () {
  "use strict";

  const ITERS = (typeof PBKDF2_ITERS !== "undefined") ? PBKDF2_ITERS : 600000;

  /* ── accept strings for each share type ── */
  const ACCEPT = {
    file:       [
      ".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",
      ".txt",".csv",".rtf",".odt",".ods",".odp",
      ".xml",".json",".md",".log",".epub",".pages",".numbers",".key"
    ].join(","),

    folder:     ".zip,.rar,.7z,.tar,.gz,.bz2,.xz,.tar.gz,.tar.bz2,.tgz,.iso",

    multimedia: [
      ".jpg",".jpeg",".png",".gif",".webp",".svg",".bmp",".tiff",".heic",".avif",
      ".mp4",".webm",".mov",".avi",".mkv",".flv",".wmv",".m4v",".3gp"
    ].join(","),

    movie:      ".mp4,.mkv,.avi,.mov,.wmv,.flv,.m4v,.webm,.mpeg,.mpg,.3gp,.ts,.vob",
  };

  /* ── drop zone labels per type ── */
  const LABELS = {
    file:       { icon:"📄", title:"Drop a document or click to browse",    sub:"PDF, Word, Excel, PPT, TXT, CSV…" },
    folder:     { icon:"📦", title:"Drop a folder archive or click to browse", sub:"ZIP, RAR, 7Z, TAR, GZ…" },
    multimedia: { icon:"🖼", title:"Drop an image or video or click to browse", sub:"JPG, PNG, GIF, MP4, WebM, MOV…" },
    movie:      { icon:"🎬", title:"Drop a movie file or click to browse",   sub:"MP4, MKV, AVI, MOV, WMV… (Subscription required)" },
  };

  /* ── DOM refs ── */
  const dropZone       = document.getElementById("dropZone");
  const fileInput      = document.getElementById("fileInput");
  const fileInfo       = document.getElementById("fileInfo");
  const fileNameEl     = document.getElementById("fileName");
  const fileSizeEl     = document.getElementById("fileSize");
  const dropIconEl     = document.getElementById("dropIcon");
  const dropTitleEl    = document.getElementById("dropTitle");
  const dropSubEl      = document.getElementById("dropSub");
  const uploadError    = document.getElementById("uploadError");
  const progressWrap   = document.getElementById("progressWrap");
  const progressLabel  = document.getElementById("progressLabel");
  const progressFill   = document.getElementById("progressFill");
  const createBtn      = document.getElementById("createShareBtn");
  const uploadArea     = document.getElementById("uploadArea");
  const resultArea     = document.getElementById("resultArea");
  const otpCodeEl      = document.getElementById("otpCode");
  const channelMsgEl   = document.getElementById("channelMsg");
  const shareAnotherBtn= document.getElementById("shareAnotherBtn");
  const guestOverlay   = document.getElementById("guestLimitOverlay");
  const overlayOtp     = document.getElementById("overlayOtp");

  let selectedFile    = null;
  let selectedType    = "file";   // current share type pill value
  let selectedChannel = "screen";

  /* ── helpers ── */
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
    if (progressFill)  progressFill.classList.add("indeterminate");
  }
  function hideProgress() {
    progressWrap && progressWrap.classList.add("hidden");
    progressFill && progressFill.classList.remove("indeterminate");
  }
  function checkReady() {
    if (createBtn) createBtn.disabled = !selectedFile;
  }

  /* ── Update drop zone UI and file input accept when type changes ── */
  function applyShareType(type) {
    selectedType  = type;
    selectedFile  = null;
    if (fileInfo)    fileInfo.classList.add("hidden");
    if (fileInput)   { fileInput.value = ""; fileInput.accept = ACCEPT[type] || ""; }
    clearErr();
    checkReady();

    const lbl = LABELS[type] || LABELS.file;
    if (dropIconEl)  dropIconEl.textContent  = lbl.icon;
    if (dropTitleEl) dropTitleEl.textContent = lbl.title;
    if (dropSubEl)   dropSubEl.textContent   = lbl.sub;

    // Show hint text below the pills
    const hints = {
      file:       "PDF, Word, Excel, PowerPoint, TXT, CSV, and more",
      folder:     "ZIP, RAR, 7Z, TAR, GZ — folder archives only",
      multimedia: "Images (JPG, PNG, GIF, WebP…) and Videos (MP4, MOV, WebM…)",
      movie:      "🎬 Subscription required · MP4, MKV, AVI, MOV, WMV…",
    };
    const hintEl = document.getElementById("shareTypeHint");
    if (hintEl) {
      hintEl.textContent = hints[type] || "";
      hintEl.style.color = type === "movie" ? "var(--orange)" : "var(--text-dim)";
    }

    // Movie type: golden glow on drop zone
    if (dropZone) {
      dropZone.style.borderColor = type === "movie"
        ? "rgba(245,158,11,0.6)"
        : "";
      dropZone.style.boxShadow = type === "movie"
        ? "0 0 20px rgba(245,158,11,0.1)"
        : "";
    }
  }

  /* ── Share-type pills ── */
  document.querySelectorAll(".channel-pill[data-sharetype]").forEach(pill => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".channel-pill[data-sharetype]").forEach(p => {
        p.classList.remove("active");
        p.setAttribute("aria-pressed", "false");
      });
      pill.classList.add("active");
      pill.setAttribute("aria-pressed", "true");
      applyShareType(pill.dataset.sharetype);
    });
  });

  /* ── OTP channel pills ── */
  document.querySelectorAll(".channel-pill[data-channel]").forEach(pill => {
    pill.addEventListener("click", () => {
      selectedChannel = pill.dataset.channel;
      document.querySelectorAll(".channel-pill[data-channel]").forEach(p => {
        p.classList.remove("active");
        p.setAttribute("aria-pressed", "false");
      });
      pill.classList.add("active");
      pill.setAttribute("aria-pressed", "true");
    });
  });

  /* ── File selection ── */
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

  // Apply initial type on page load
  applyShareType("file");
  // Re-apply if a pill is already marked active (e.g. page rehydration)
  const activePill = document.querySelector(".channel-pill.active[data-sharetype]");
  if (activePill) applyShareType(activePill.dataset.sharetype);

  /* ── Main upload ── */
  if (createBtn) {
    createBtn.addEventListener("click", async () => {
      clearErr();
      if (!selectedFile) { showErr("Please select a file first."); return; }

      createBtn.disabled = true;

      try {
        /* Step 1: Fetch OTP from server (server stores it, browser uses it to encrypt) */
        setProgress("Preparing secure upload…");
        let otpRes;
        try {
          otpRes = await fetch("/shares/get-otp/", {
            method: "GET",
            headers: { "X-CSRFToken": csrf() },
          });
        } catch {
          hideProgress(); showErr("Network error — check your connection.");
          createBtn.disabled = false; return;
        }
        const otpData = await otpRes.json().catch(() => null);
        if (!otpData || !otpData.ok) {
          hideProgress(); showErr("Could not generate access code. Please try again.");
          createBtn.disabled = false; return;
        }
        const otp = otpData.otp;  // this is the server-generated OTP

        const shareType = selectedType;

        /* Step 2: Encrypt file using the server OTP */
        setProgress("Encrypting your file…");
        let enc;
        try {
          enc = await AskMyCrypto.encryptFile(selectedFile, otp, ITERS);
        } catch (err) {
          hideProgress(); showErr("Encryption failed: " + err.message);
          createBtn.disabled = false; return;
        }

        /* Step 3: Upload ciphertext */
        setProgress("Uploading encrypted file…");
        const form = new FormData();
        form.append("file_data",         new Blob([enc.ciphertext], { type: "application/octet-stream" }), "payload.bin");
        form.append("salt_b64",          enc.saltB64);
        form.append("iv_b64",            enc.ivB64);
        form.append("algorithm_version", enc.algorithmVersion);
        form.append("original_filename", selectedFile.name);
        form.append("share_type",        shareType);

        let res;
        try {
          res = await fetch("/shares/create/", {
            method:  "POST",
            headers: { "X-CSRFToken": csrf() },
            body:    form,
          });
        } catch {
          hideProgress(); showErr("Network error — check your connection.");
          createBtn.disabled = false; return;
        }

        hideProgress();
        const data = await res.json().catch(() => null);
        if (!data) { showErr("Unexpected server response."); createBtn.disabled = false; return; }

        /* ── Error handling ── */
        if (res.status === 429 && data.error === "quota_exceeded") {
          showErr("Daily limit reached. Sign in or upgrade your plan.");
          createBtn.disabled = false; return;
        }
        if (!data.ok) {
          const MAP = {
            file_too_large:             "File exceeds the allowed size limit.",
            missing_file:               "No file received by server.",
            missing_crypto_params:      "Encryption params missing — please try again.",
            unverified_account:         "Please complete account verification first.",
            otp_session_missing:        "Session expired — please refresh and try again.",
            movie_requires_account:     "You must be signed in to share movies.",
            movie_requires_subscription:"🎬 Movie sharing requires a paid subscription (Starter, Plus, or Pro).",
            movie_too_large:            `🎬 Movie exceeds your plan's limit (${data.limit_gb || "?"}GB). Upgrade to a higher plan.`,
          };
          showErr(MAP[data.error] || ("Error: " + (data.error || "unknown")));
          createBtn.disabled = false; return;
        }

        /* Step 4: Show result — use the OTP returned from server */
        const shownOtp = data.otp || otp;

        if (uploadArea)  uploadArea.classList.add("hidden");
        if (resultArea)  resultArea.classList.remove("hidden");
        if (otpCodeEl)   otpCodeEl.textContent = shownOtp;

        if (channelMsgEl) {
          const chMap = {
            email:  "📧 The access code was also sent to your registered email.",
            sms:    "📱 The access code was also sent to your registered phone.",
            screen: "👆 Share this 6-digit code with your recipient privately.",
          };
          channelMsgEl.textContent = chMap[data.channel] || "";
        }

        /* guest quota hit → forced overlay */
        if (data.guest_quota_hit) {
          if (overlayOtp) overlayOtp.textContent = shownOtp;
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

  /* ── Share another ── */
  if (shareAnotherBtn) {
    shareAnotherBtn.addEventListener("click", () => {
      selectedFile = null;
      if (uploadArea)  uploadArea.classList.remove("hidden");
      if (resultArea)  resultArea.classList.add("hidden");
      if (fileInfo)    fileInfo.classList.add("hidden");
      if (fileInput)   fileInput.value = "";
      clearErr();
      if (createBtn) createBtn.disabled = true;
      // Reset to first type pill
      const first = document.querySelector(".channel-pill[data-sharetype]");
      if (first) { first.click(); }
    });
  }

  /* ── Guest overlay: no dismiss ── */
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
      const els = [...guestOverlay.querySelectorAll(
        'a[href],button:not([disabled]),input,[tabindex]:not([tabindex="-1"])'
      )];
      if (!els.length) return;
      if (e.shiftKey && document.activeElement === els[0]) {
        e.preventDefault(); els[els.length - 1].focus();
      } else if (!e.shiftKey && document.activeElement === els[els.length - 1]) {
        e.preventDefault(); els[0].focus();
      }
    });
  }

})();
