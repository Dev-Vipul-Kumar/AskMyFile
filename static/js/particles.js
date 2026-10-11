/**
 * AskMyFile – Particle background canvas
 * Lightweight floating dot network with neon colors.
 * Respects prefers-reduced-motion.
 */
(function () {
  "use strict";
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const canvas = document.getElementById("particles-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  let W, H, particles = [], raf;
  const COUNT    = Math.min(60, Math.floor(window.innerWidth / 24));
  const COLORS   = ["rgba(0,245,255,", "rgba(179,71,255,", "rgba(255,79,163,"];
  const SPEED    = 0.35;
  const MAX_DIST = 140;

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  function Particle() {
    this.x  = Math.random() * W;
    this.y  = Math.random() * H;
    this.vx = (Math.random() - 0.5) * SPEED;
    this.vy = (Math.random() - 0.5) * SPEED;
    this.r  = Math.random() * 1.5 + 0.5;
    this.c  = COLORS[Math.floor(Math.random() * COLORS.length)];
    this.a  = Math.random() * 0.5 + 0.2;
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

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.c + p.a + ")";
      ctx.fill();

      for (let j = i + 1; j < particles.length; j++) {
        const q   = particles[j];
        const dx  = p.x - q.x;
        const dy  = p.y - q.y;
        const dist= Math.sqrt(dx*dx + dy*dy);
        if (dist < MAX_DIST) {
          const alpha = (1 - dist/MAX_DIST) * 0.15;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.strokeStyle = p.c + alpha + ")";
          ctx.lineWidth   = 0.6;
          ctx.stroke();
        }
      }
    }
    raf = requestAnimationFrame(draw);
  }

  window.addEventListener("resize", () => { resize(); init(); });
  resize(); init(); draw();
})();
