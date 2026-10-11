/**
 * AskMyFile – OTP-based Crypto Module
 * The 6-digit OTP IS the encryption key source (PBKDF2 → AES-256-GCM).
 * No passphrase UI — the OTP is generated server-side and shown/sent to sender.
 *
 * Envelope (plaintext before encryption):
 *   [4 bytes magic "AMFO"]
 *   [2 bytes filename length uint16BE]
 *   [N bytes UTF-8 filename]
 *   [2 bytes MIME length uint16BE]
 *   [M bytes UTF-8 MIME]
 *   [remaining: raw file bytes]
 */
const AskMyCrypto = (() => {
  "use strict";

  const MAGIC     = new TextEncoder().encode("AMFO");
  const ALGO      = "AES-GCM";
  const KEY_BITS  = 256;
  const IV_BYTES  = 12;
  const SALT_BYTES= 16;
  const TAG_BITS  = 128;

  /* ── utils ── */
  const rand   = n => crypto.getRandomValues(new Uint8Array(n));
  const enc    = new TextEncoder();
  const dec    = new TextDecoder();

  function toB64(bytes) {
    return btoa(String.fromCharCode(...bytes))
      .replace(/\+/g,"-").replace(/\//g,"_").replace(/=/g,"");
  }
  function fromB64(s) {
    s = s.replace(/-/g,"+").replace(/_/g,"/");
    while (s.length % 4) s += "=";
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
  }
  function concat(...arrays) {
    const out = new Uint8Array(arrays.reduce((s,a) => s+a.length, 0));
    let off = 0;
    for (const a of arrays) { out.set(a, off); off += a.length; }
    return out;
  }
  function u16be(n) { return new Uint8Array([(n>>8)&0xff, n&0xff]); }
  function readu16be(buf, off) { return (buf[off]<<8)|buf[off+1]; }

  /* ── key derivation ── */
  async function deriveKey(otp, salt, iterations) {
    const km = await crypto.subtle.importKey(
      "raw", enc.encode(otp), {name:"PBKDF2"}, false, ["deriveKey"]
    );
    return crypto.subtle.deriveKey(
      { name:"PBKDF2", salt, iterations, hash:"SHA-256" },
      km,
      { name:ALGO, length:KEY_BITS },
      false,
      ["encrypt","decrypt"]
    );
  }

  /* ── envelope ── */
  function buildEnvelope(filename, mime, fileBytes) {
    const fn = enc.encode(filename || "file");
    const mt = enc.encode(mime    || "application/octet-stream");
    return concat(MAGIC, u16be(fn.length), fn, u16be(mt.length), mt, new Uint8Array(fileBytes));
  }
  function parseEnvelope(plain) {
    for (let i=0;i<MAGIC.length;i++)
      if (plain[i]!==MAGIC[i]) throw new Error("Bad envelope magic");
    let off = MAGIC.length;
    const fnLen = readu16be(plain, off); off+=2;
    const filename = dec.decode(plain.slice(off, off+fnLen)); off+=fnLen;
    const mtLen = readu16be(plain, off); off+=2;
    const mimeType = dec.decode(plain.slice(off, off+mtLen)); off+=mtLen;
    return { filename, mimeType, fileBytes: plain.slice(off) };
  }

  /* ── encrypt ── */
  async function encryptFile(file, otp, iterations=600000) {
    const fileBytes = new Uint8Array(await file.arrayBuffer());
    const envelope  = buildEnvelope(file.name, file.type, fileBytes);
    const salt = rand(SALT_BYTES);
    const iv   = rand(IV_BYTES);
    const key  = await deriveKey(otp, salt, iterations);
    const ctBuf = await crypto.subtle.encrypt({name:ALGO, iv, tagLength:TAG_BITS}, key, envelope);
    return {
      ciphertext: new Uint8Array(ctBuf),
      saltB64: toB64(salt),
      ivB64:   toB64(iv),
      algorithmVersion: "AES-GCM-256-v1",
    };
  }

  /* ── decrypt ── */
  async function decryptFile(ciphertext, otp, saltB64, ivB64, iterations=600000) {
    const salt = fromB64(saltB64);
    const iv   = fromB64(ivB64);
    const key  = await deriveKey(otp, salt, iterations);
    let plain;
    try {
      plain = await crypto.subtle.decrypt({name:ALGO, iv, tagLength:TAG_BITS}, key, ciphertext);
    } catch {
      throw new Error("Decryption failed — incorrect OTP or corrupted file.");
    }
    return parseEnvelope(new Uint8Array(plain));
  }

  /* ── download helper ── */
  function triggerDownload(bytes, filename, mime) {
    const blob = new Blob([bytes], {type: mime||"application/octet-stream"});
    const url  = URL.createObjectURL(blob);
    const a    = Object.assign(document.createElement("a"), {href:url, download:filename});
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1500);
  }

  return { encryptFile, decryptFile, triggerDownload, toB64, fromB64 };
})();
