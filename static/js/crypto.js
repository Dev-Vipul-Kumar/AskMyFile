const AskMyCrypto = (() => {
  "use strict";

  const MAGIC     = new TextEncoder().encode("AMFO");
  const ALGO      = "AES-GCM";
  const KEY_BITS  = 256;
  const IV_BYTES  = 12;
  const SALT_BYTES= 16;
  const TAG_BITS  = 128;

  /**
   * Chunk size for reading large files to avoid OOM.
   * AES-GCM encrypts the entire payload at once (no streaming mode in WebCrypto),
   * so we chunk the FILE READ, not the encryption. However, for very large files
   * (>500MB) even the encryption can OOM the browser. We handle this by:
   * 1) Reading the file in chunks into an ArrayBuffer (avoids double-buffering).
   * 2) Using a single encrypt/decrypt call.
   *
   * For files larger than ~1.5GB, we split into multiple encrypted segments,
   * each with its own GCM tag, to stay within browser memory limits.
   */
  const SEGMENT_SIZE = 64 * 1024 * 1024; // 64 MB segments for large file chunked encryption

  const rand = n => crypto.getRandomValues(new Uint8Array(n));
  const enc  = new TextEncoder();
  const dec  = new TextDecoder();

  function toB64(bytes) {
    // For large arrays, avoid spread operator which can stack overflow
    let binary = '';
    const len = bytes.length;
    for (let i = 0; i < len; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary).replace(/\+/g,"-").replace(/\//g,"_").replace(/=/g,"");
  }

  function fromB64(s) {
    s = s.replace(/-/g,"+").replace(/_/g,"/");
    while (s.length % 4) s += "=";
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
  }

  function concat(...arrays) {
    const totalLen = arrays.reduce((s,a) => s + a.length, 0);
    const out = new Uint8Array(totalLen);
    let off = 0;
    for (const a of arrays) { out.set(a, off); off += a.length; }
    return out;
  }

  function u16be(n) { return new Uint8Array([(n>>8)&0xff, n&0xff]); }
  function u32be(n) { return new Uint8Array([(n>>24)&0xff, (n>>16)&0xff, (n>>8)&0xff, n&0xff]); }
  function readu16be(buf, off) { return (buf[off]<<8)|buf[off+1]; }
  function readu32be(buf, off) { return (buf[off]<<24)|(buf[off+1]<<16)|(buf[off+2]<<8)|buf[off+3]; }

  async function deriveKey(otp, salt, iterations) {
    const km = await crypto.subtle.importKey("raw", enc.encode(otp), {name:"PBKDF2"}, false, ["deriveKey"]);
    return crypto.subtle.deriveKey(
      { name:"PBKDF2", salt, iterations, hash:"SHA-256" },
      km,
      { name:ALGO, length:KEY_BITS },
      false,
      ["encrypt","decrypt"]
    );
  }

  function buildEnvelope(filename, mime, fileBytes) {
    const fn = enc.encode(filename || "file");
    const mt = enc.encode(mime    || "application/octet-stream");
    return concat(MAGIC, u16be(fn.length), fn, u16be(mt.length), mt, new Uint8Array(fileBytes));
  }

  function parseEnvelope(plain) {
    for (let i=0; i<MAGIC.length; i++)
      if (plain[i] !== MAGIC[i]) throw new Error("Bad envelope magic");
    let off = MAGIC.length;
    const fnLen   = readu16be(plain, off); off += 2;
    const filename = dec.decode(plain.slice(off, off+fnLen)); off += fnLen;
    const mtLen   = readu16be(plain, off); off += 2;
    const mimeType = dec.decode(plain.slice(off, off+mtLen)); off += mtLen;
    return { filename, mimeType, fileBytes: plain.slice(off) };
  }

  /**
   * Read a File object into a Uint8Array using chunked reading
   * to avoid reading the entire file via arrayBuffer() which can OOM.
   */
  async function readFileChunked(file) {
    // For files under 50MB, use the simple approach
    if (file.size < 50 * 1024 * 1024) {
      return new Uint8Array(await file.arrayBuffer());
    }

    // For larger files, read in chunks via FileReader to be more memory-friendly
    const CHUNK = 16 * 1024 * 1024; // 16MB read chunks
    const result = new Uint8Array(file.size);
    let offset = 0;

    while (offset < file.size) {
      const end = Math.min(offset + CHUNK, file.size);
      const slice = file.slice(offset, end);
      const buf = await slice.arrayBuffer();
      result.set(new Uint8Array(buf), offset);
      offset = end;
      // Allow GC between chunks
      await new Promise(r => setTimeout(r, 0));
    }

    return result;
  }

  /**
   * Encrypt file — for large files, uses segmented encryption.
   * Each segment is individually encrypted with an incrementing IV
   * to stay within browser memory limits.
   *
   * Format for segmented files:
   *   [4B "AMFS"] [4B segment_count] [4B segment_size]
   *   For each segment:
   *     [4B ciphertext_length] [ciphertext_bytes]
   *
   * Regular (small) files use the original single-shot format.
   */
  async function encryptFile(file, otp, iterations=100000) {
    const fileBytes = await readFileChunked(file);
    const envelope  = buildEnvelope(file.name, file.type, fileBytes);

    const salt = rand(SALT_BYTES);
    const iv   = rand(IV_BYTES);
    const key  = await deriveKey(otp, salt, iterations);

    // For files under ~200MB, use single-shot encryption (most common case)
    if (envelope.length < 200 * 1024 * 1024) {
      const ctBuf = await crypto.subtle.encrypt({name:ALGO, iv, tagLength:TAG_BITS}, key, envelope);
      return {
        ciphertext:       new Uint8Array(ctBuf),
        saltB64:          toB64(salt),
        ivB64:            toB64(iv),
        algorithmVersion: "AES-GCM-256-v1",
      };
    }

    // ── Segmented encryption for large files ──
    const segmentCount = Math.ceil(envelope.length / SEGMENT_SIZE);
    const segments = [];

    for (let i = 0; i < segmentCount; i++) {
      const start = i * SEGMENT_SIZE;
      const end   = Math.min(start + SEGMENT_SIZE, envelope.length);
      const chunk = envelope.slice(start, end);

      // Derive unique IV per segment by incrementing
      const segIv = new Uint8Array(iv);
      const counter = i;
      segIv[IV_BYTES-1] = (segIv[IV_BYTES-1] + counter) & 0xff;
      segIv[IV_BYTES-2] = (segIv[IV_BYTES-2] + (counter >> 8)) & 0xff;

      const ctBuf = await crypto.subtle.encrypt({name:ALGO, iv:segIv, tagLength:TAG_BITS}, key, chunk);
      segments.push(new Uint8Array(ctBuf));

      // Allow GC and UI updates between segments
      await new Promise(r => setTimeout(r, 0));
    }

    // Build segmented output: AMFS header + segments
    const marker = new TextEncoder().encode("AMFS");
    let totalLen = marker.length + 4 + 4; // marker + count + segment_size
    for (const seg of segments) {
      totalLen += 4 + seg.length; // length prefix + ciphertext
    }

    const output = new Uint8Array(totalLen);
    let off = 0;
    output.set(marker, off); off += marker.length;
    output.set(u32be(segmentCount), off); off += 4;
    output.set(u32be(SEGMENT_SIZE), off); off += 4;

    for (const seg of segments) {
      output.set(u32be(seg.length), off); off += 4;
      output.set(seg, off); off += seg.length;
    }

    return {
      ciphertext:       output,
      saltB64:          toB64(salt),
      ivB64:            toB64(iv),
      algorithmVersion: "AES-GCM-256-v1",
    };
  }

  /**
   * Decrypt — handles both single-shot and segmented ciphertext.
   */
  async function decryptFile(ciphertext, otp, saltB64, ivB64, iterations=100000) {
    const salt = fromB64(saltB64);
    const iv   = fromB64(ivB64);
    const key  = await deriveKey(otp, salt, iterations);

    // Check if this is segmented format (starts with "AMFS")
    const segMarker = new TextEncoder().encode("AMFS");
    const isSegmented = ciphertext.length > 12 &&
      ciphertext[0] === segMarker[0] &&
      ciphertext[1] === segMarker[1] &&
      ciphertext[2] === segMarker[2] &&
      ciphertext[3] === segMarker[3];

    let plainBytes;

    if (!isSegmented) {
      // ── Single-shot decryption (original format) ──
      let plain;
      try {
        plain = await crypto.subtle.decrypt({name:ALGO, iv, tagLength:TAG_BITS}, key, ciphertext);
      } catch {
        throw new Error("Decryption failed — incorrect code or corrupted file.");
      }
      plainBytes = new Uint8Array(plain);
    } else {
      // ── Segmented decryption ──
      let off = segMarker.length;
      const segmentCount = readu32be(ciphertext, off); off += 4;
      const segmentSize  = readu32be(ciphertext, off); off += 4;

      const plainParts = [];
      let totalPlainLen = 0;

      for (let i = 0; i < segmentCount; i++) {
        const ctLen = readu32be(ciphertext, off); off += 4;
        const ctChunk = ciphertext.slice(off, off + ctLen); off += ctLen;

        // Reconstruct segment IV
        const segIv = new Uint8Array(iv);
        segIv[IV_BYTES-1] = (segIv[IV_BYTES-1] + i) & 0xff;
        segIv[IV_BYTES-2] = (segIv[IV_BYTES-2] + (i >> 8)) & 0xff;

        let plainChunk;
        try {
          plainChunk = await crypto.subtle.decrypt({name:ALGO, iv:segIv, tagLength:TAG_BITS}, key, ctChunk);
        } catch {
          throw new Error("Decryption failed — incorrect code or corrupted file.");
        }

        plainParts.push(new Uint8Array(plainChunk));
        totalPlainLen += plainChunk.byteLength;

        // Allow GC between segments
        await new Promise(r => setTimeout(r, 0));
      }

      // Combine all decrypted segments
      plainBytes = new Uint8Array(totalPlainLen);
      let pOff = 0;
      for (const part of plainParts) {
        plainBytes.set(part, pOff);
        pOff += part.length;
      }
    }

    return parseEnvelope(plainBytes);
  }

  function triggerDownload(bytes, filename, mime) {
    const blob = new Blob([bytes], {type: mime || "application/octet-stream"});
    const url  = URL.createObjectURL(blob);
    const a    = Object.assign(document.createElement("a"), {href:url, download:filename});
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 3000);
  }

  async function sha256hex(str) {
    const buf = await crypto.subtle.digest("SHA-256", enc.encode(str));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2,"0")).join("");
  }

  return { encryptFile, decryptFile, triggerDownload, toB64, fromB64, sha256hex };
})();
