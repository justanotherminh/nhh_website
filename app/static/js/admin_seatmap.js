// Admin seat map, in two modes (set by data-mode on #seatmap):
//
//   "export" (default) — the invitations page. Only un-issued VIP seats are
//       clickable; "Tạo vé PDF" mints their tickets into the depot.
//   "pool"             — the VIP pool page. Clicking an available seat stages it to
//       join the pool; clicking an un-issued VIP seat stages it to go back on sale.
//
// Both share the SVG render and pan/zoom; only what's selectable and what the
// action button does differ. Selection is client-side until the button is pressed.
(function () {
  "use strict";
  const SVGNS = "http://www.w3.org/2000/svg";
  const root = document.getElementById("seatmap");
  if (!root) return;
  const POOL = (root.dataset.mode || "export") === "pool";
  const panel = document.getElementById("selection");
  const summary = document.getElementById("selection-summary");
  // The invitations page calls it #export-btn, the pool page #apply-btn.
  const actionBtn = document.getElementById("export-btn")
                 || document.getElementById("apply-btn");
  const statusEl = document.getElementById("export-status");

  const selected = new Map(); // seatId -> { seat, g, name, action }

  // A summary survives the reload that follows a successful pool edit.
  const CARRY = "vipPoolMsg";
  const carried = sessionStorage.getItem(CARRY);
  if (carried) {
    sessionStorage.removeItem(CARRY);
    const parsed = JSON.parse(carried);
    setTimeout(() => setStatus(parsed.msg, parsed.isErr), 0);
  }

  function setStatus(msg, isErr) {
    if (!statusEl) return;
    statusEl.textContent = msg || "";
    statusEl.classList.toggle("is-error", !!isErr);
  }

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
      el("rect", { x: r.x, y: r.y, width: r.w, height: r.h, rx: 8, class: "floor-region" }, svg);
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
    const seatReg = new Map(); // <g> -> entry, for the seats this mode can act on
    data.seats.forEach((s) => {
      const tier = tierById[s.tier_id];
      const rank = tier ? tier.rank : 0;
      const g = el("g", { class: "seat-g tier-r" + rank }, svg);

      // Three VIP states: unexported (pickable), exported, and sent to the guest.
      const vipState = s.vip ? s.vip_state : "none";
      let cls = "seat seat-nonvip";
      if (s.vip) {
        cls = vipState === "sent" ? "seat seat-vip-sent"
            : vipState === "exported" ? "seat seat-vip-done"
            : "seat seat-vip";
      } else if (POOL && s.status === "booked") {
        cls = "seat seat-taken";          // sold: can never join the pool
      } else if (POOL && s.status === "available" && !s.held) {
        cls = "seat";                     // keep the tier colour — tier matters
      }                                   // when choosing which seats to give away
      const rect = el("rect", { x: s.x, y: s.y, width: sz, height: sz, rx: 3, class: cls }, g);
      el("text", { x: s.x + sz / 2, y: s.y + sz / 2, class: "seat-num", "text-anchor": "middle", "dominant-baseline": "central" }, g).textContent = s.num;

      // What, if anything, clicking this seat does in this mode.
      let action = null;
      if (!POOL) {
        if (s.vip && vipState === "none") action = "export";
      } else if (s.vip && vipState === "none") {
        action = "release";
      } else if (!s.vip && s.status === "available" && !s.held) {
        action = "add";
      }

      if (action) {
        g.style.cursor = "pointer";
        const hint = action === "add" ? " — thêm vào vé mời"
                   : action === "release" ? " — trả về bán"
                   : "";
        el("title", {}, g).textContent = s.label + hint;
        seatReg.set(g, { seat: s, g, rect, action });
      } else {
        // Say why it's locked, so a manager isn't left clicking a dead seat.
        const why = vipState === "sent" ? " — đã gửi cho khách"
                  : vipState === "exported" ? " — đã xuất vé"
                  : s.status === "booked" ? " — đã bán"
                  : s.held ? " — khách đang giữ chỗ"
                  : s.status === "blocked" ? " — đang khóa"
                  : "";
        if (s.vip || POOL) el("title", {}, g).textContent = s.label + why;
      }
    });

    function toggle(entry) {
      const { seat, g, action } = entry;
      // The two pool actions get different colours: adding and releasing pull in
      // opposite directions and a mixed batch is easy to misread otherwise.
      const cls = action === "release" ? "pick-release"
                : action === "add" ? "pick-add"
                : "selected";
      if (selected.has(seat.id)) { selected.delete(seat.id); g.classList.remove(cls); }
      else { selected.set(seat.id, { seat, g, name: "", action }); g.classList.add(cls); }
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

    // Pool: apply the staged membership changes, then reload so the map redraws
    // from the server rather than from an optimistic guess.
    async function applyPool() {
      const add = [], release = [];
      selected.forEach((e) => (e.action === "add" ? add : release).push(e.seat.id));
      actionBtn.disabled = true;
      setStatus("Đang cập nhật…");
      try {
        const r = await fetch("/admin/vip-seats/apply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ add, release }),
        });
        const data = await r.json().catch(() => null);
        if (!r.ok || !data || !data.ok) {
          setStatus("Có lỗi khi cập nhật, vui lòng thử lại.", true);
          actionBtn.disabled = false;
          return;
        }
        const bits = [];
        if (data.added) bits.push(`${data.added} ghế chuyển sang vé mời`);
        if (data.released) bits.push(`${data.released} ghế trả về bán`);
        const errs = data.errors || [];
        if (errs.length) bits.push(`${errs.length} ghế không đổi được: ${errs.join("; ")}`);
        const msg = bits.join(" · ") || "Không có thay đổi nào.";
        if (data.added || data.released) {
          // Something changed, so the map is stale — carry the summary over the reload.
          sessionStorage.setItem(CARRY, JSON.stringify({ msg, isErr: errs.length > 0 }));
          window.location.reload();
          return;
        }
        setStatus(msg, true);
        actionBtn.disabled = false;
      } catch (_) {
        setStatus("Không kết nối được máy chủ.", true);
        actionBtn.disabled = false;
      }
    }

    // Export: generate + store a PDF per selected seat, then go to the depot page.
    actionBtn?.addEventListener("click", async () => {
      if (!selected.size) return;
      if (POOL) { applyPool(); return; }
      // Names are optional (for the organisers' records only — never on the ticket).
      const tickets = [...selected.values()].map((e) => ({
        seat_id: e.seat.id, name: (e.name || "").trim(),
      }));
      actionBtn.disabled = true;
      setStatus("Đang tạo vé…");
      try {
        const r = await fetch("/admin/invitations/export", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tickets }),
        });
        const data = await r.json().catch(() => null);
        if (r.ok && data && data.ok && (!data.errors || !data.errors.length)) {
          // Freshly generated — view/download them in the depot.
          window.location.href = "/admin/invitations/tickets";
          return;
        }
        const errs = (data && data.errors) || [];
        setStatus(
          errs.length
            ? `Tạo ${data.created} vé, ${errs.length} lỗi: ${errs.join("; ")}`
            : "Có lỗi khi tạo vé, vui lòng thử lại.",
          true,
        );
        actionBtn.disabled = false;
      } catch (_) {
        setStatus("Không kết nối được máy chủ.", true);
        actionBtn.disabled = false;
      }
    });

    window.addEventListener("resize", fit);
    requestAnimationFrame(fit);
    updatePanel();
  }

  function updatePanel() {
    if (!panel) return;
    panel.innerHTML = "";
    // Rebuilt only when the selection changes (not on keystrokes), so typing into
    // a name field is never interrupted; the field writes straight to entry.name.
    const items = [...selected.values()].sort(
      (a, b) => a.seat.label.localeCompare(b.seat.label, "vi")
    );
    items.forEach((entry) => {
      const li = document.createElement("li");
      li.className = POOL ? "vip-pick pool-pick" : "vip-pick";
      const label = document.createElement("span");
      label.className = "vip-pick__seat";
      label.textContent = entry.seat.label;
      li.appendChild(label);
      if (POOL) {
        // No name field here — this page changes what a seat *is*, it doesn't
        // issue anything. Spell out the direction instead.
        const act = document.createElement("span");
        act.className = "pool-pick__action pool-pick__action--" + entry.action;
        act.textContent = entry.action === "add" ? "→ vé mời" : "→ mở bán";
        li.appendChild(act);
      } else {
        const input = document.createElement("input");
        input.type = "text";
        input.className = "vip-pick__name";
        input.placeholder = "Tên người nhận (không bắt buộc)";
        input.value = entry.name || "";
        input.addEventListener("input", () => { entry.name = input.value; });
        li.appendChild(input);
      }
      panel.appendChild(li);
    });
    if (summary) {
      if (!selected.size) {
        summary.textContent = "Chưa chọn ghế nào.";
      } else if (POOL) {
        const add = items.filter((e) => e.action === "add").length;
        const rel = items.length - add;
        summary.textContent = [
          add ? `${add} ghế → vé mời` : "",
          rel ? `${rel} ghế → mở bán` : "",
        ].filter(Boolean).join(" · ");
      } else {
        summary.textContent = `${selected.size} ghế`;
      }
    }
    if (actionBtn) actionBtn.disabled = selected.size === 0;
  }
})();
