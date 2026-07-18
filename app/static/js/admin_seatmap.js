// Admin invitation seat map: only VIP-reserved seats are clickable. Select them and
// "Xuất vé in" opens a printable sheet of their tickets (minting any missing ones).
// Same SVG render + pan/zoom as the buyer map, but selection is client-side only.
(function () {
  "use strict";
  const SVGNS = "http://www.w3.org/2000/svg";
  const root = document.getElementById("seatmap");
  if (!root) return;
  const panel = document.getElementById("selection");
  const summary = document.getElementById("selection-summary");
  const exportBtn = document.getElementById("continue-btn");
  const exportForm = document.getElementById("exportForm");
  const exportIds = document.getElementById("export-seat-ids");

  const selected = new Map(); // seatId -> seat

  function el(name, attrs, parent) {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  fetch("/admin/invitations/map")
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

    const bv = data.viewBox.split(" ").map(Number);
    const content = { x: bv[0], y: bv[1], w: bv[2], h: bv[3] };

    // ---- floor blocks ----
    (data.floorRegions || []).forEach((r) => {
      el("path", { d: r.d, class: "floor-region" }, svg);
      const t = el("text", {
        x: r.cx, y: r.cy, class: "floor-label",
        "text-anchor": "middle", "dominant-baseline": "central",
      }, svg);
      t.textContent = r.floor;
    });

    // ---- architecture ----
    data.architecture.forEach((a) => {
      el("rect", { x: a.x, y: a.y, width: a.w, height: a.h, rx: 3, class: "arch arch-" + a.type }, svg);
      const cx = a.x + a.w / 2, cy = a.y + a.h / 2;
      const attrs = { x: cx, y: cy, class: "arch-label", "text-anchor": "middle", "dominant-baseline": "central" };
      if (a.h > a.w) attrs.transform = `rotate(-90 ${cx} ${cy})`;
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
    const seatReg = new Map(); // <g> -> entry (VIP seats only)
    data.seats.forEach((s) => {
      const tier = tierById[s.tier_id];
      const rank = tier ? tier.rank : 0;
      const g = el("g", { class: "seat-g tier-r" + rank }, svg);
      const cls = s.vip ? (s.exported ? "seat seat-vip-done" : "seat seat-vip") : "seat seat-nonvip";
      const rect = el("rect", { x: s.x, y: s.y, width: sz, height: sz, rx: 3, class: cls }, g);
      el("text", { x: s.x + sz / 2, y: s.y + sz / 2, class: "seat-num", "text-anchor": "middle", "dominant-baseline": "central" }, g).textContent = s.num;
      if (s.vip) {
        g.style.cursor = "pointer";
        el("title", {}, g).textContent = s.label + (s.exported ? " — đã xuất vé" : "");
        seatReg.set(g, { seat: s, g, rect });
      }
    });

    function toggle(entry) {
      const { seat, g } = entry;
      if (selected.has(seat.id)) { selected.delete(seat.id); g.classList.remove("selected"); }
      else { selected.set(seat.id, seat); g.classList.add("selected"); }
      updatePanel();
    }

    // ===== zoom / pan via viewBox (same as the buyer map) =====
    const vb = { x: content.x, y: content.y, w: content.w, h: content.h };
    let fitW = content.w;
    let dragged = false;
    const MIN_W = 240;

    function aspect() {
      const r = svg.getBoundingClientRect();
      return r.height / r.width || content.h / content.w;
    }
    function apply() {
      vb.h = vb.w * aspect();
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
      fitW = Math.max(content.w, content.h / a);
      vb.w = fitW;
      vb.h = fitW * a;
      vb.x = content.x + content.w / 2 - vb.w / 2;
      vb.y = content.y + content.h / 2 - vb.h / 2;
      apply();
    }
    function zoomAt(cx, cy, factor) {
      const r = svg.getBoundingClientRect();
      const fx = (cx - r.left) / r.width, fy = (cy - r.top) / r.height;
      const ux = vb.x + fx * vb.w, uy = vb.y + fy * vb.h;
      vb.w = Math.min(fitW, Math.max(MIN_W, vb.w / factor));
      vb.h = vb.w * aspect();
      vb.x = ux - fx * vb.w;
      vb.y = uy - fy * vb.h;
      apply();
    }

    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    }, { passive: false });

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
        if (entry) toggle(entry);
      }
      downSeat = null;
    });
    svg.addEventListener("pointercancel", (e) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) lastDist = 0;
      downSeat = null;
    });

    const center = () => {
      const r = svg.getBoundingClientRect();
      return { cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
    };
    document.getElementById("zoom-in")?.addEventListener("click", () => { const c = center(); zoomAt(c.cx, c.cy, 1.4); });
    document.getElementById("zoom-out")?.addEventListener("click", () => { const c = center(); zoomAt(c.cx, c.cy, 1 / 1.4); });
    document.getElementById("zoom-reset")?.addEventListener("click", fit);

    // Export: put the selected ids on the form; mark them exported optimistically.
    exportForm?.addEventListener("submit", (e) => {
      if (!selected.size) { e.preventDefault(); return; }
      exportIds.value = [...selected.keys()].join(",");
      const done = [...selected.keys()];
      setTimeout(() => {
        done.forEach((id) => {
          seatReg.forEach((entry) => {
            if (entry.seat.id === id) {
              entry.rect.classList.remove("seat-vip");
              entry.rect.classList.add("seat-vip-done");
              entry.g.classList.remove("selected");
            }
          });
          selected.delete(id);
        });
        updatePanel();
      }, 400);
    });

    window.addEventListener("resize", fit);
    requestAnimationFrame(fit);
    updatePanel();
  }

  function updatePanel() {
    if (!panel) return;
    panel.innerHTML = "";
    const items = [...selected.values()].sort((a, b) => a.label.localeCompare(b.label, "vi"));
    items.forEach((seat) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${seat.label}</span>`;
      panel.appendChild(li);
    });
    if (summary) summary.textContent = selected.size ? `${selected.size} ghế` : "Chưa chọn ghế nào.";
    if (exportBtn) exportBtn.disabled = selected.size === 0;
  }
})();
