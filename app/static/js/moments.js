// Featured-moment reel: a vertical column of photo cards that scrolls continuously
// and loops seamlessly. It drifts upward on its own; moving the pointer toward the
// top or bottom of the reel scrolls it that way (faster near the edges). The fade
// at the top/bottom edges is a CSS mask on .reel. Works for any number of images.
// Self-contained, no dependencies. Respects prefers-reduced-motion.
(function () {
  "use strict";
  const reel = document.querySelector(".reel");
  const track = document.querySelector(".reel-track");
  if (!reel || !track) return;

  const originals = Array.prototype.slice.call(track.children);
  if (!originals.length) return;

  const DRIFT = -0.35;      // gentle upward drift (px/frame); negative = up
  const MAX = 3.2;          // top speed when the pointer is at an edge
  const DEADZONE = 0.12;    // middle band with no pointer override
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let period = 0;           // pixel height of one full set (the loop period)

  // Clone whole sets until the track is tall enough to cover the reel plus one
  // spare set, so wrapping by one period is always seamless and gap-free.
  function build() {
    // reset to just the originals
    while (track.children.length > originals.length) {
      track.removeChild(track.lastChild);
    }
    let guard = 0;
    while (
      track.scrollHeight < reel.clientHeight + track.scrollHeight / track.children.length * originals.length &&
      guard++ < 8
    ) {
      originals.forEach((el) => track.appendChild(el.cloneNode(true)));
    }
    // ensure at least two sets exist for a valid period measurement
    if (track.children.length < originals.length * 2) {
      originals.forEach((el) => track.appendChild(el.cloneNode(true)));
    }
    period = track.children[originals.length].offsetTop - track.children[0].offsetTop;
  }

  let y = 0, vel = DRIFT, raf = 0;
  function frame() {
    y += vel;
    if (period > 0) {
      if (y <= -period) y += period;
      else if (y > 0) y -= period;
    }
    track.style.transform = "translateY(" + y.toFixed(2) + "px)";
    raf = requestAnimationFrame(frame);
  }

  if (!reduce) {
    reel.addEventListener("pointermove", function (e) {
      const r = reel.getBoundingClientRect();
      const ny = (e.clientY - r.top) / r.height - 0.5;   // -0.5 (top) .. 0.5 (bottom)
      if (Math.abs(ny) < DEADZONE) { vel = DRIFT; return; }
      const k = (ny - Math.sign(ny) * DEADZONE) / (0.5 - DEADZONE); // ±1 at edges
      vel = -k * MAX;   // pointer near top -> scroll down; near bottom -> scroll up
    });
    reel.addEventListener("pointerleave", function () { vel = DRIFT; });
  }

  let rz = 0;
  window.addEventListener("resize", function () {
    clearTimeout(rz);
    rz = setTimeout(function () { y = 0; build(); }, 150);
  });

  let started = false;
  function start() {
    if (started) return;
    started = true;
    build();
    if (!reduce) raf = requestAnimationFrame(frame);
  }

  // Card heights come from CSS (aspect-ratio), so layout is valid immediately;
  // build now for the initial position, then start the loop (load-safe).
  build();
  if (document.readyState === "complete") start();
  else window.addEventListener("load", start);
})();
