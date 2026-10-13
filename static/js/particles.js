/**
 * AskMyFile – Particle background canvas
 * Forest-green palette: #C6F135 accent particles on #090B09 background.
 * Respects prefers-reduced-motion.
 */
(function () {
  "use strict";
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const canvas = document.getElementById("particles-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  let W, H, particles = [], raf;
  const COUNT    = Math.min(55, Math.floor(window.innerWidth / 26));
  // Green palette particles
  const COLORS   = [
    "rgba(198,241,53,",    // accent lime-green
    "rgba(217,255,99,",    // hover green
    "rgba(125,219,107,",   // soft green
    "rgba(245,246,240,",   // near-white
  ];
  const SPEED    = 0.3;
  const MAX_DIST = 130;

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  function Particle() {
    this.x  = Math.random() * W;
    this.y  = Math.random() * H;
    this.vx = (Math.random() - 0.5) * SPEED;
    this.vy = (Math.random() - 0.5) * SPEED;
    this.r  = Math.random() * 1.6 + 0.4;
    this.c  = COLORS[Math.floor(Math.random() * COLORS.length)];
    this.a  = Math.random() * 0.45 + 0.1;
  }

  function init() {
    particles = [];
    for (let i = 0; i < COUNT; i++) particles.push(new Particle());
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);

    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > W) p.vx *= -1;
      if (p.y < 0 || p.y > H) p.vy *= -1;

      // Draw dot
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.c + p.a + ")";
      ctx.fill();

      // Draw connection lines
      for (let j = i + 1; j < particles.length; j++) {
        const q    = particles[j];
        const dx   = p.x - q.x;
        const dy   = p.y - q.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < MAX_DIST) {
          const alpha = (1 - dist / MAX_DIST) * 0.12;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.strokeStyle = p.c + alpha + ")";
          ctx.lineWidth   = 0.7;
          ctx.stroke();
        }
      }
    }

    raf = requestAnimationFrame(draw);
  }

  window.addEventListener("resize", () => { resize(); init(); });
  resize(); init(); draw();
})();
