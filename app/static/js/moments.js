// Featured-moment carousel: wide photo cards evenly spaced around a circular hub
// that sits just off the right edge of the hero. Each card orbits the hub and is
// rotated so its horizontal centreline points straight at the hub (radial spokes).
// The wheel rolls continuously while the pointer is near the top/bottom of the
// hero (top -> one direction, bottom -> the other); the mid-height band is neutral.
// Cards do nothing but orbit. Self-contained, no dependencies.
(function () {
  "use strict";
  const hero = document.querySelector(".home-hero");
  const wrap = document.querySelector(".home-hero__featured");
  const strip = document.querySelector(".moments");
  if (!hero || !wrap || !strip) return;

  const cards = Array.prototype.slice.call(strip.querySelectorAll(".moment"));
  if (!cards.length) return;

  const N = cards.length;
  const TWO_PI = Math.PI * 2;
  const base = cards.map((_, i) => (i / N) * TWO_PI);   // even spacing around circle
  const MAX_SPEED = 0.0125;                              // rad/frame at the extremes
  const DEADZONE = 0.12;                                 // mid band that doesn't roll
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let cx = 0, cy = 0, R = 0;
  function measure() {
    const r = wrap.getBoundingClientRect();
    R = Math.min(r.height * 0.42, 360);
    cx = r.width - 24;          // hub just inside the right edge
    cy = r.height / 2;
  }

  function layout() {
    for (let i = 0; i < N; i++) {
      const t = base[i] + rot;                 // this card's angle on the wheel
      const x = cx + R * Math.cos(t);
      const y = cy + R * Math.sin(t);
      // Radial rotation, offset by pi so left-side cards read upright (not flipped).
      const phi = t - Math.PI;
      // Front = left side of the hub (cos t < 0); fade out toward/behind the hub.
      const front = -Math.cos(t);
      const op = Math.max(0, Math.min(1, (front + 0.05) / 0.55));
      const c = cards[i];
      const z = String(Math.round((front + 1) * 100));
      c.style.opacity = op.toFixed(3);
      c.dataset.z = z;
      if (c.style.zIndex !== "999") c.style.zIndex = z;   // keep a hovered card on top
      c.style.transform =
        "translate(" + x.toFixed(1) + "px," + y.toFixed(1) + "px) " +
        "translate(-50%,-50%) rotate(" + phi.toFixed(4) + "rad) scale(var(--scale,1))";
    }
  }

  // Lift a hovered card to the top and let it zoom smoothly even while idle.
  cards.forEach(function (c) {
    c.addEventListener("pointerenter", function () { c.style.zIndex = "999"; });
    c.addEventListener("pointerleave", function () { c.style.zIndex = c.dataset.z || "1"; });
  });

  let rot = 0, vel = 0, running = false;
  function tick() {
    if (Math.abs(vel) < 1e-5) { running = false; strip.classList.remove("rolling"); return; }
    rot = (rot + vel) % TWO_PI;
    layout();
    requestAnimationFrame(tick);
  }
  function start() {
    if (!running) { running = true; strip.classList.add("rolling"); requestAnimationFrame(tick); }
  }

  if (!reduce) {
    // Only roll while the pointer is over the carousel area, not the whole hero.
    wrap.addEventListener("pointermove", function (e) {
      const r = wrap.getBoundingClientRect();
      const ny = (e.clientY - r.top) / r.height - 0.5;   // -0.5 (top) .. 0.5 (bottom)
      if (Math.abs(ny) < DEADZONE) { vel = 0; return; }
      const k = (ny - Math.sign(ny) * DEADZONE) / (0.5 - DEADZONE); // 0..±1
      vel = k * MAX_SPEED;                                // top -> negative, bottom -> positive
      start();
    });
    wrap.addEventListener("pointerleave", function () { vel = 0; });
  }

  let rz = 0;
  window.addEventListener("resize", function () {
    clearTimeout(rz);
    rz = setTimeout(function () { measure(); layout(); }, 120);
  });

  measure();
  layout();
})();
