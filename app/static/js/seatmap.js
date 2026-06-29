// Self-contained SVG seat-map renderer with zoom/pan + selection.
// Fetches /api/seatmap, draws the hall, lets the user pick up to maxPerOrder seats.
// Zoom: mouse wheel, +/-/reset buttons, pinch (touch). Pan: drag / one-finger drag.
(function () {
  "use strict";
  const SVGNS = "http://www.w3.org/2000/svg";
  const fmtVnd = (n) => n.toLocaleString("vi-VN") + " đ";

  const root = document.getElementById("seatmap");
  if (!root) return;
  const panel = document.getElementById("selection");
  const summary = document.getElementById("selection-summary");
  const continueBtn = document.getElementById("continue-btn");

  const selected = new Map(); // seatId -> {seat, tier}

  function el(name, attrs, parent) {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function darken(hex, f = 0.55) {
    const n = parseInt(hex.slice(1), 16);
    return `rgb(${Math.round(((n >> 16) & 255) * f)},${Math.round(((n >> 8) & 255) * f)},${Math.round((n & 255) * f)})`;
  }

  fetch("/api/seatmap")
    .then((r) => r.json())
    .then((data) => render(data))
    .catch((err) => {
      root.innerHTML = "<p class='error'>Không tải được sơ đồ chỗ ngồi.</p>";
      console.error(err);
    });

  function render(data) {
    const tierById = {};
    data.tiers.forEach((t) => (tierById[t.id] = t));

    const svg = el("svg", { class: "seatmap-svg", preserveAspectRatio: "xMidYMid meet" });
    root.innerHTML = "";
    root.appendChild(svg);

    // ---- content bounds (from the data's viewBox) ----
    const bv = data.viewBox.split(" ").map(Number);
    const content = { x: bv[0], y: bv[1], w: bv[2], h: bv[3] };

    // ---- architecture ----
    data.architecture.forEach((a) => {
      el("rect", { x: a.x, y: a.y, width: a.w, height: a.h, rx: 3, class: "arch arch-" + a.type }, svg);
      const cx = a.x + a.w / 2, cy = a.y + a.h / 2;
      const attrs = { x: cx, y: cy, class: "arch-label", "text-anchor": "middle", "dominant-baseline": "central" };
      if (a.h > a.w) attrs.transform = `rotate(-90 ${cx} ${cy})`; // vertical box -> vertical text
      el("text", attrs, svg).textContent = a.label;
    });

    // ---- stage ----
    const st = data.stage;
    el("rect", { x: st.x, y: st.y, width: st.w, height: st.h, rx: 6, class: "stage" }, svg);
    el("text", { x: st.x + st.w / 2, y: st.y + st.h / 2, class: "stage-label", "text-anchor": "middle", "dominant-baseline": "central" }, svg).textContent = st.label;

    // ---- row markers ----
    data.rowMarkers.forEach((m) => {
      el("text", { x: m.x + data.seat / 2, y: m.y + data.seat / 2, class: "row-marker", "text-anchor": "middle", "dominant-baseline": "central" }, svg).textContent = m.label;
    });

    // ---- seats ----
    const sz = data.seat;
    const seatReg = new Map(); // <g> element -> {seat, rect, g, tier}  (available only)
    data.seats.forEach((s) => {
      const tier = tierById[s.tier_id];
      const g = el("g", { class: "seat-g" }, svg);
      const rect = el("rect", {
        x: s.x, y: s.y, width: sz, height: sz, rx: 3,
        class: "seat seat-" + s.status,
        fill: tier ? tier.color : "#ccc",
        stroke: tier ? darken(tier.color) : "#999",
      }, g);
      el("text", { x: s.x + sz / 2, y: s.y + sz / 2, class: "seat-num", "text-anchor": "middle", "dominant-baseline": "central" }, g).textContent = s.num;
      if (s.status === "available") {
        g.style.cursor = "pointer";
        el("title", {}, g).textContent = `${s.label} — ${tier ? fmtVnd(tier.price) : ""}`;
        seatReg.set(g, { seat: s, rect, g, tier });
      }
    });

    function toggle(seat, rect, g, tier) {
      if (selected.has(seat.id)) {
        selected.delete(seat.id);
        g.classList.remove("selected");
      } else {
        if (selected.size >= data.maxPerOrder) {
          alert("Bạn chỉ có thể chọn tối đa " + data.maxPerOrder + " ghế mỗi lần.");
          return;
        }
        selected.set(seat.id, { seat, tier });
        g.classList.add("selected");
      }
      updatePanel();
    }

    // ===== zoom / pan via viewBox =====
    const vb = { x: content.x, y: content.y, w: content.w, h: content.h };
    let MIN_W = 240;        // most zoomed-in
    let fitW = content.w;   // most zoomed-out (set in fit())
    let dragged = false;

    function aspect() {
      const r = svg.getBoundingClientRect();
      return r.height / r.width || content.h / content.w;
    }
    function apply() {
      // keep viewBox aspect equal to the element's aspect (no letterboxing)
      vb.h = vb.w * aspect();
      // clamp pan so content stays roughly in view
      const maxX = content.x + content.w - vb.w * 0.15;
      const minX = content.x - vb.w * 0.85;
      const maxY = content.y + content.h - vb.h * 0.15;
      const minY = content.y - vb.h * 0.85;
      vb.x = Math.min(maxX, Math.max(minX, vb.x));
      vb.y = Math.min(maxY, Math.max(minY, vb.y));
      svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    }
    function fit() {
      const a = aspect();
      // width needed so both content dimensions fit at this aspect
      fitW = Math.max(content.w, content.h / a);
      vb.w = fitW;
      vb.h = fitW * a;
      vb.x = content.x + content.w / 2 - vb.w / 2;
      vb.y = content.y + content.h / 2 - vb.h / 2;
      apply();
    }
    function clientFrac(cx, cy) {
      const r = svg.getBoundingClientRect();
      return { fx: (cx - r.left) / r.width, fy: (cy - r.top) / r.height };
    }
    function zoomAt(cx, cy, factor) {
      const { fx, fy } = clientFrac(cx, cy);
      const ux = vb.x + fx * vb.w;
      const uy = vb.y + fy * vb.h;
      vb.w = Math.min(fitW, Math.max(MIN_W, vb.w / factor));
      vb.h = vb.w * aspect();
      vb.x = ux - fx * vb.w;
      vb.y = uy - fy * vb.h;
      apply();
    }

    // wheel zoom
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    }, { passive: false });

    // pointer drag + pinch.  A non-dragging tap toggles the pressed seat
    // (we can't rely on the `click` event because pointer-capture retargets it).
    const pointers = new Map();
    let lastDist = 0, downX = 0, downY = 0, downSeat = null;
    svg.addEventListener("pointerdown", (e) => {
      svg.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      dragged = false;
      downX = e.clientX; downY = e.clientY;
      const g = e.target.closest && e.target.closest(".seat-g");
      downSeat = g && seatReg.get(g) ? g : null;
      if (pointers.size === 2) {
        const p = [...pointers.values()];
        lastDist = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
      }
    });
    svg.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId)) return;
      const prev = pointers.get(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1) {
        const r = svg.getBoundingClientRect();
        vb.x -= (e.clientX - prev.x) * (vb.w / r.width);
        vb.y -= (e.clientY - prev.y) * (vb.h / r.height);
        apply();
        if (Math.hypot(e.clientX - downX, e.clientY - downY) > 4) dragged = true;
      } else if (pointers.size === 2) {
        const p = [...pointers.values()];
        const dist = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
        const mid = { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 };
        if (lastDist) zoomAt(mid.x, mid.y, dist / lastDist);
        lastDist = dist;
        dragged = true;
      }
    });
    svg.addEventListener("pointerup", (e) => {
      const wasSingle = pointers.size === 1;
      pointers.delete(e.pointerId);
      if (pointers.size < 2) lastDist = 0;
      if (wasSingle && !dragged && downSeat) {
        const entry = seatReg.get(downSeat);
        if (entry) toggle(entry.seat, entry.rect, entry.g, entry.tier);
      }
      downSeat = null;
    });
    function cancelPointer(e) {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) lastDist = 0;
      downSeat = null;
    }
    svg.addEventListener("pointercancel", cancelPointer);

    // buttons
    const center = () => {
      const r = svg.getBoundingClientRect();
      return { cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
    };
    document.getElementById("zoom-in")?.addEventListener("click", () => { const c = center(); zoomAt(c.cx, c.cy, 1.4); });
    document.getElementById("zoom-out")?.addEventListener("click", () => { const c = center(); zoomAt(c.cx, c.cy, 1 / 1.4); });
    document.getElementById("zoom-reset")?.addEventListener("click", fit);

    window.addEventListener("resize", fit);
    // initial fit (rAF so the element has been laid out)
    requestAnimationFrame(fit);
    updatePanel();
  }

  function updatePanel() {
    if (!panel) return;
    panel.innerHTML = "";
    let total = 0;
    const items = [...selected.values()].sort((a, b) => a.seat.label.localeCompare(b.seat.label, "vi"));
    items.forEach(({ seat, tier }) => {
      const price = tier ? tier.price : 0;
      total += price;
      const li = document.createElement("li");
      li.innerHTML = `<span>${seat.label}</span><span>${fmtVnd(price)}</span>`;
      panel.appendChild(li);
    });
    if (summary) summary.textContent = selected.size ? `${selected.size} ghế — ${fmtVnd(total)}` : "Chưa chọn ghế nào.";
    if (continueBtn) continueBtn.disabled = selected.size === 0;
  }
})();
