/**
 * AskMyFile – OTP-based Upload Flow
 * 1. User picks a file → 2. JS encrypts with a random OTP  →
 * 3. Uploads ciphertext + crypto params to /shares/create/  →
 * 4. Server returns raw OTP (screen channel) or sends via email/SMS  →
 * 5. Sender shares the 6-digit code with the recipient out-of-band.
 */
(function () {
  "use strict";

  /* ── PBKDF2 iteration count injected by template ── */
  const ITERS = (typeof PBKDF2_ITERS !== "undefined") ? PBKDF2_ITERS : 600000;

  /* ── DOM refs ── */
  const dropZone       = document.getElementById("dropZone");
  const fileInput      = document.getElementById("fileInput");
  const fileInfo       = document.getElementById("fileInfo");
  const fileNameEl     = document.getElementById("fileName");
  const fileSizeEl     = document.getElementById("fileSize");
  const channelSection = document.getElementById("channelSection");
  const channelPills   = document.querySelectorAll(".channel-pill");
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

  /* ── Guest quota overlay overlay OTP display ── */
  let selectedFile  = null;
  let selectedChannel = "screen"; // default

  /* ── helpers ── */
  function fmt(bytes) {
    if (bytes < 1024)       return bytes + " B";
    if (bytes < 1048576)    return (bytes/1024).toFixed(1) + " KB";
    return (bytes/1048576).toFixed(2) + " MB";
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

  /* ── File selection ── */
  function handleFile(file) {
    if (!file) return;
    selectedFile = file;
    if (fileInfo)    fileInfo.classList.remove("hidden");
    if (fileNameEl)  fileNameEl.textContent = file.name;
    if (fileSizeEl)  fileSizeEl.textContent = fmt(file.size);
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
      if (f) { if (fileInput) { const dt = new DataTransfer(); dt.items.add(f); fileInput.files = dt.files; } handleFile(f); }
    });
  }

  /* ── Channel pills ── */
  channelPills.forEach(pill => {
    pill.addEventListener("click", () => {
      selectedChannel = pill.dataset.channel;
      channelPills.forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
    });
  });

  /* ── Main upload ── */
  if (createBtn) {
    createBtn.addEventListener("click", async () => {
      clearErr();
      if (!selectedFile) { showErr("Please select a file first."); return; }

      createBtn.disabled = true;

      try {
        /* Step 1: generate ephemeral OTP for encryption */
        const otp = Array.from(crypto.getRandomValues(new Uint8Array(6)))
          .map(b => b % 10).join("");

        /* Determine share type */
        const shareTypeBtn = document.querySelector(".channel-pill.active[data-sharetype]");
        const shareType = shareTypeBtn ? shareTypeBtn.dataset.sharetype : "file";

        /* Step 2: encrypt */
        setProgress("Encrypting your file…");
        let enc;
        try {
          enc = await AskMyCrypto.encryptFile(selectedFile, otp, ITERS);
        } catch (err) {
          hideProgress(); showErr("Encryption failed: " + err.message);
          createBtn.disabled = false; return;
        }

        /* Step 3: upload */
        setProgress("Uploading encrypted file…");
        const form = new FormData();
        form.append("file_data", new Blob([enc.ciphertext], {type:"application/octet-stream"}), "payload.bin");
        form.append("salt_b64",            enc.saltB64);
        form.append("iv_b64",              enc.ivB64);
        form.append("algorithm_version",   enc.algorithmVersion);
        form.append("original_filename",   selectedFile.name);
        form.append("otp_channel",         selectedChannel);
        form.append("share_type",          shareType);

        let res;
        try {
          res = await fetch("/shares/create/", {
            method: "POST",
            headers: { "X-CSRFToken": csrf() },
            body: form,
          });
        } catch {
          hideProgress(); showErr("Network error — check your connection.");
          createBtn.disabled = false; return;
        }

        hideProgress();
        const data = await res.json().catch(() => null);
        if (!data) { showErr("Unexpected server response."); createBtn.disabled = false; return; }

        if (res.status === 429 && data.error === "quota_exceeded") {
          showErr("Daily limit reached. Sign in or register for more shares.");
          createBtn.disabled = false; return;
        }
        if (!data.ok) {
          const MAP = { file_too_large:"File exceeds the 100 MB limit.", missing_file:"No file received.",
            missing_crypto_params:"Crypto params missing.", unverified_account:"Complete account verification first." };
          showErr(MAP[data.error] || ("Error: " + (data.error || "unknown")));
          createBtn.disabled = false; return;
        }

        /* Step 4: show result */
        const shownOtp = data.otp || otp; // server returns OTP for screen channel

        if (uploadArea)  uploadArea.classList.add("hidden");
        if (resultArea)  resultArea.classList.remove("hidden");
        if (otpCodeEl)   otpCodeEl.textContent = shownOtp;

        /* channel message */
        if (channelMsgEl) {
          const chMap = {
            email: "📧 The access code was also sent to your registered email.",
            sms:   "📱 The access code was also sent to your registered phone.",
            screen: "👆 Share this code with your recipient privately.",
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
      if (e.shiftKey && document.activeElement === els[0])        { e.preventDefault(); els[els.length-1].focus(); }
      else if (!e.shiftKey && document.activeElement === els[els.length-1]) { e.preventDefault(); els[0].focus(); }
    });
  }

})();
