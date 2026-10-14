(function () {
  "use strict";

  const ITERS = (typeof PBKDF2_ITERS !== "undefined") ? PBKDF2_ITERS : 100000;

  const boxes      = [...document.querySelectorAll(".otp-box")];
  const decryptBtn = document.getElementById("decryptBtn");
  const errorBox   = document.getElementById("decryptError");
  const progressW  = document.getElementById("progressWrap");
  const progressL  = document.getElementById("progressLabel");
  const progressF  = document.getElementById("progressFill");
  const successBox = document.getElementById("decryptSuccess");
  const statusText = document.getElementById("statusText");

  boxes.forEach((box, idx) => {
    box.addEventListener("input", () => {
      box.value = box.value.replace(/\D/g,"").slice(-1);
      box.classList.toggle("filled", !!box.value);
      if (box.value && idx < boxes.length-1) boxes[idx+1].focus();
      checkReady();
    });
    box.addEventListener("keydown", e => {
      if (e.key === "Backspace" && !box.value && idx > 0) {
        boxes[idx-1].value = ""; boxes[idx-1].classList.remove("filled"); boxes[idx-1].focus();
      }
      if (e.key === "ArrowLeft"  && idx > 0)               boxes[idx-1].focus();
      if (e.key === "ArrowRight" && idx < boxes.length-1)  boxes[idx+1].focus();
      if (e.key === "Enter") decryptBtn && decryptBtn.click();
    });
    box.addEventListener("paste", e => {
      e.preventDefault();
      const text = (e.clipboardData||window.clipboardData).getData("text").replace(/\D/g,"").slice(0,6);
      [...text].forEach((ch, i) => {
        if (boxes[idx+i]) { boxes[idx+i].value = ch; boxes[idx+i].classList.add("filled"); }
      });
      boxes[Math.min(idx+text.length, boxes.length-1)].focus();
      checkReady();
    });
  });

  function getOtp()    { return boxes.map(b => b.value).join(""); }
  function checkReady(){ if (decryptBtn) decryptBtn.disabled = getOtp().length !== 6; }
  checkReady();

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

  function setProgressPct(label, pct) {
    if (!progressW) return;
    progressW.classList.remove("hidden");
    if (progressL) progressL.textContent = label;
    if (progressF) {
      progressF.classList.remove("indeterminate");
      progressF.style.width = pct + "%";
    }
  }

  function hideProgress() {
    progressW && progressW.classList.add("hidden");
    if (progressF) {
      progressF.classList.remove("indeterminate");
      progressF.style.width = "0%";
    }
  }

  if (!decryptBtn) return;

  /**
   * Download large files using chunked reading via ReadableStream
   * to avoid loading the entire response into memory at once with arrayBuffer().
   * Falls back to arrayBuffer() if ReadableStream is not available.
   */
  async function downloadChunked(url, totalSize) {
    const response = await fetch(url, { method: "GET" });

    if (!response.ok) {
      return { ok: false, status: response.status };
    }

    // If the file is small (<50MB), just use arrayBuffer
    if (!totalSize || totalSize < 50 * 1024 * 1024 || !response.body) {
      const buf = await response.arrayBuffer();
      return { ok: true, data: new Uint8Array(buf) };
    }

    // Stream the response body into a pre-allocated buffer
    const reader = response.body.getReader();
    const result = new Uint8Array(totalSize);
    let received = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (received + value.length > totalSize) {
        // More data than expected, reallocate
        const newBuf = new Uint8Array(received + value.length + 1024 * 1024);
        newBuf.set(result.subarray(0, received));
        newBuf.set(value, received);
        received += value.length;
        return { ok: true, data: newBuf.subarray(0, received) };
      }
      result.set(value, received);
      received += value.length;

      // Update progress
      const pct = Math.round((received / totalSize) * 100);
      setProgressPct(`Downloading… ${pct}%`, pct);
    }

    return { ok: true, data: result.subarray(0, received) };
  }

  decryptBtn.addEventListener("click", async () => {
    clearErr();
    const otp = getOtp();
    if (otp.length !== 6) { showErr("Please enter all 6 digits."); return; }

    decryptBtn.disabled = true;

    try {
      setProgress("Verifying access code…");
      if (window.AskMyLoader) AskMyLoader.show();

      const csrfToken = (typeof CSRF_TOKEN !== "undefined" && CSRF_TOKEN)
        ? CSRF_TOKEN
        : (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";

      let verifyRes;
      try {
        verifyRes = await fetch("/shares/access/", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
          body: JSON.stringify({ otp }),
        });
      } catch {
        hideProgress(); showErr("Network error — check your connection.");
        if (window.AskMyLoader) AskMyLoader.hide();
        decryptBtn.disabled = false; return;
      }

      const vdata = await verifyRes.json().catch(() => null);
      if (!vdata || !vdata.ok) {
        hideProgress();
        const eMap = {
          otp_invalid:       "Incorrect code. Please try again.",
          invalid_otp_format:"Code must be exactly 6 digits.",
          share_unavailable: "This share has expired or is no longer available.",
        };
        showErr(eMap[vdata?.error] || "Invalid code.");
        if (window.AskMyLoader) AskMyLoader.hide();
        decryptBtn.disabled = false; return;
      }

      const { share_id, salt_b64, iv_b64, pbkdf2_iterations, share_type, ciphertext_size } = vdata;

      // Use chunked download for large files to avoid OOM
      setProgress("Downloading encrypted content…");

      const dlResult = await downloadChunked(`/shares/${share_id}/download/`, ciphertext_size);

      if (!dlResult.ok) {
        hideProgress();
        showErr(dlResult.status === 403 ? "Session expired — please re-enter your code." : "Content unavailable.");
        if (window.AskMyLoader) AskMyLoader.hide();
        decryptBtn.disabled = false; return;
      }

      setProgress("Decrypting in your browser…");
      const ctBuf = dlResult.data;

      let result;
      try {
        result = await AskMyCrypto.decryptFile(
          ctBuf, otp, salt_b64, iv_b64,
          pbkdf2_iterations || ITERS
        );
      } catch (err) {
        hideProgress();
        showErr("Decryption failed. The code may be wrong or the content was tampered with.");
        if (window.AskMyLoader) AskMyLoader.hide();
        decryptBtn.disabled = false; return;
      }

      hideProgress();
      if (window.AskMyLoader) AskMyLoader.hide();

      if (share_type === "text") {
        const textContent = new TextDecoder().decode(result.fileBytes);
        if (successBox) {
          successBox.classList.remove("hidden");
          const textOut = document.getElementById("decryptedText");
          if (textOut) {
            textOut.textContent  = textContent;
            textOut.style.display = "";
          } else {
            const pre = document.createElement("pre");
            pre.style.cssText = "background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:0.75rem;color:var(--text);font-family:var(--font);font-size:0.85rem;white-space:pre-wrap;word-break:break-word;margin-top:0.75rem;max-height:300px;overflow-y:auto;";
            pre.textContent = textContent;
            successBox.appendChild(pre);
          }
        }
        if (statusText) statusText.textContent = "Text decrypted successfully!";
        decryptBtn.textContent = "✅ Decrypted!";
      } else {
        AskMyCrypto.triggerDownload(result.fileBytes, result.filename, result.mimeType);
        if (successBox) successBox.classList.remove("hidden");
        decryptBtn.textContent = "✅ Downloaded!";
        if (statusText) statusText.textContent = `Saved as "${result.filename}"`;
      }

    } catch (err) {
      hideProgress(); showErr("Unexpected error: " + err.message);
      if (window.AskMyLoader) AskMyLoader.hide();
      decryptBtn.disabled = false;
    }
  });

})();
