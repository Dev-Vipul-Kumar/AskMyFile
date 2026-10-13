/**
 * AskMyFile – OTP-based Decryption Flow (recipient side)
 * 1. Recipient enters 6-digit OTP into individual boxes  →
 * 2. POST /shares/access/ to verify  →
 * 3. GET /shares/<id>/download/ to fetch ciphertext  →
 * 4. Decrypt with OTP in browser  →  save file.
 */
(function () {
  "use strict";

  const ITERS = (typeof PBKDF2_ITERS !== "undefined") ? PBKDF2_ITERS : 600000;

  /* ── OTP boxes ── */
  const boxes     = [...document.querySelectorAll(".otp-box")];
  const decryptBtn= document.getElementById("decryptBtn");
  const errorBox  = document.getElementById("decryptError");
  const progressW = document.getElementById("progressWrap");
  const progressL = document.getElementById("progressLabel");
  const progressF = document.getElementById("progressFill");
  const successBox= document.getElementById("decryptSuccess");
  const statusText= document.getElementById("statusText");

  /* ── OTP box interaction ── */
  boxes.forEach((box, idx) => {
    box.addEventListener("input", () => {
      box.value = box.value.replace(/\D/g,"").slice(-1);
      if (box.value) {
        box.classList.add("filled");
        if (idx < boxes.length - 1) boxes[idx+1].focus();
      } else {
        box.classList.remove("filled");
      }
      checkReady();
    });
    box.addEventListener("keydown", e => {
      if (e.key === "Backspace" && !box.value && idx > 0) {
        boxes[idx-1].value = "";
        boxes[idx-1].classList.remove("filled");
        boxes[idx-1].focus();
      }
      if (e.key === "ArrowLeft"  && idx > 0) { e.preventDefault(); boxes[idx-1].focus(); }
      if (e.key === "ArrowRight" && idx < boxes.length-1) { e.preventDefault(); boxes[idx+1].focus(); }
      if (e.key === "Enter") decryptBtn && decryptBtn.click();
    });
    box.addEventListener("paste", e => {
      e.preventDefault();
      const text = (e.clipboardData||window.clipboardData).getData("text").replace(/\D/g,"").slice(0,6);
      [...text].forEach((ch, i) => {
        if (boxes[idx+i]) {
          boxes[idx+i].value = ch;
          boxes[idx+i].classList.add("filled");
        }
      });
      const next = Math.min(idx + text.length, boxes.length-1);
      boxes[next].focus();
      checkReady();
    });
  });

  function getOtp() { return boxes.map(b=>b.value).join(""); }
  function checkReady() {
    if (decryptBtn) decryptBtn.disabled = getOtp().length !== 6;
  }
  checkReady();

  /* ── helpers ── */
  function showErr(msg) {
    if (!errorBox) return;
    errorBox.textContent = "⚠ " + msg;
    errorBox.classList.remove("hidden");
    boxes.forEach(b => b.style.borderColor = "var(--red)");
    setTimeout(() => boxes.forEach(b => b.style.borderColor = ""), 2000);
  }
  function clearErr() {
    errorBox && errorBox.classList.add("hidden");
    boxes.forEach(b => b.style.borderColor = "");
  }
  function setProgress(label) {
    if (!progressW) return;
    progressW.classList.remove("hidden");
    if (progressL) progressL.textContent = label;
    if (progressF) progressF.classList.add("indeterminate");
  }
  function hideProgress() {
    progressW && progressW.classList.add("hidden");
    progressF && progressF.classList.remove("indeterminate");
  }
  function setStatus(msg) { statusText && (statusText.textContent = msg); }

  /* ── Main decrypt flow ── */
  if (!decryptBtn) return;

  decryptBtn.addEventListener("click", async () => {
    clearErr();
    const otp = getOtp();
    if (otp.length !== 6) { showErr("Please enter all 6 digits."); return; }

    decryptBtn.disabled = true;

    try {
      /* Step 1: Verify OTP with server */
      setProgress("Verifying access code…");
      let verifyRes;
      try {
        // Use injected CSRF_TOKEN first (most reliable), then fall back to cookie
        const csrfToken = (typeof CSRF_TOKEN !== "undefined" && CSRF_TOKEN)
          ? CSRF_TOKEN
          : (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";

        verifyRes = await fetch("/shares/access/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          body: JSON.stringify({ otp }),
        });
      } catch {
        hideProgress(); showErr("Network error — check your connection."); decryptBtn.disabled = false; return;
      }

      const vdata = await verifyRes.json().catch(() => null);
      if (!vdata || !vdata.ok) {
        hideProgress();
        const eMap = {
          otp_invalid:      "Incorrect code. Please try again.",
          invalid_otp_format:"Code must be exactly 6 digits.",
          share_unavailable: "This share has expired or is no longer available.",
        };
        showErr(eMap[vdata?.error] || "Invalid code.");
        decryptBtn.disabled = false; return;
      }

      const { share_id, salt_b64, iv_b64, pbkdf2_iterations, ciphertext_size } = vdata;

      /* Step 2: Download ciphertext */
      setProgress("Downloading encrypted file…");
      let dlRes;
      try {
        dlRes = await fetch(`/shares/${share_id}/download/`, { method: "GET" });
      } catch {
        hideProgress(); showErr("Download failed — network error."); decryptBtn.disabled = false; return;
      }
      if (!dlRes.ok) {
        hideProgress();
        showErr(dlRes.status === 403 ? "Session expired — please re-enter your code."
          : "File unavailable.");
        decryptBtn.disabled = false; return;
      }

      setProgress("Decrypting in your browser…");
      const ctBuf = await dlRes.arrayBuffer();

      /* Step 3: Decrypt */
      let result;
      try {
        result = await AskMyCrypto.decryptFile(
          new Uint8Array(ctBuf), otp, salt_b64, iv_b64,
          pbkdf2_iterations || ITERS
        );
      } catch (err) {
        hideProgress();
        showErr("Decryption failed. The code may be wrong or the file was tampered with.");
        decryptBtn.disabled = false; return;
      }

      hideProgress();
      AskMyCrypto.triggerDownload(result.fileBytes, result.filename, result.mimeType);

      if (successBox) successBox.classList.remove("hidden");
      decryptBtn.textContent = "✅ Downloaded!";
      setStatus(`Saved as "${result.filename}"`);

    } catch (err) {
      hideProgress(); showErr("Unexpected error: " + err.message);
      decryptBtn.disabled = false;
    }
  });

})();
