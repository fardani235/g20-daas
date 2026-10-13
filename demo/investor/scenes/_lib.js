// Shared helpers for every scene. `record_demo.sh` wraps this file and one
// scene file into a single function for `playwright-cli run-code`, so nothing
// here may use import/require or Node APIs.
//
//   (page, cfg) => helpers
//
// cfg = { base, out, fps, width, height }
(page, cfg) => {
  // Scenes are performed `slow` times slower than they will play and sped back
  // up in the edit: the screencast cannot keep 30 unique frames a second on a
  // laptop GPU, but it can keep 10, and 10 x 3 is enough.
  const tick = ms => page.waitForTimeout(ms);
  const sleep = ms => page.waitForTimeout(ms * slow);
  const ease = t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const pos = { x: cfg.width / 2, y: cfg.height / 2 };
  const marks = {};
  let t0 = 0;
  let recording = false;
  let slow = 1;

  // A drawn cursor: the screencast does not capture the OS pointer, and the
  // built-in action cursor paces every mouse event, which makes drags crawl.
  const CURSOR = `(() => {
    if (window.__demoCursor) return;
    window.__demoCursor = true;
    const mount = () => {
      if (document.getElementById('__demo_cursor')) return;
      const c = document.createElement('div');
      c.id = '__demo_cursor';
      c.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24"><path d="M5 3l14 8.2-6.2 1.6 3.6 6.6-2.6 1.4-3.6-6.7L5 19z" fill="#111827" stroke="#ffffff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
      c.style.cssText = 'position:fixed;left:0;top:0;width:28px;height:28px;margin:-3px 0 0 -5px;pointer-events:none;z-index:2147483647;will-change:transform;filter:drop-shadow(0 2px 3px rgba(0,0,0,.45));opacity:0';
      const r = document.createElement('div');
      r.id = '__demo_ripple';
      r.style.cssText = 'position:fixed;left:0;top:0;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;pointer-events:none;z-index:2147483646;border:3px solid rgba(37,99,235,.9);background:rgba(37,99,235,.18);opacity:0';
      (document.fullscreenElement || document.documentElement).append(r, c);
      const last = JSON.parse(sessionStorage.getItem('__demo_cursor') || 'null');
      if (last) place(last.x, last.y);
    };
    const place = (x, y) => {
      const c = document.getElementById('__demo_cursor');
      if (!c) return;
      c.style.opacity = window.__demoCursorHidden ? '0' : '1';
      c.style.transform = 'translate(' + x + 'px,' + y + 'px)';
      window.__demoCursorPos = { x, y };
    };
    window.addEventListener('mousemove', e => place(e.clientX, e.clientY), true);
    window.addEventListener('mousedown', e => {
      const r = document.getElementById('__demo_ripple');
      if (!r || window.__demoCursorHidden) return;
      r.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)';
      r.animate([{ opacity: .95, scale: .35 }, { opacity: 0, scale: 1.25 }], { duration: 520 * (+sessionStorage.getItem('__demo_slow') || 1), easing: 'ease-out' });
    }, true);
    window.addEventListener('pagehide', () => {
      if (window.__demoCursorPos) sessionStorage.setItem('__demo_cursor', JSON.stringify(window.__demoCursorPos));
    });
    // Init scripts run before <html> exists; mount once there is a document.
    const boot = () => {
      mount();
      new MutationObserver(mount).observe(document.documentElement, { childList: true });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
  })()`;

  let uiScale = 1;
  const applyScale = () => page.evaluate(z => { document.body.style.zoom = z === 1 ? '' : String(z); }, uiScale).catch(() => {});

  const h = {
    sleep,
    pos,
    // Browser-style page zoom for the form/table pages, so their text reads at
    // video size. Not for the map or 3D pages (Leaflet does not expect it).
    async scale(z) { uiScale = z; await applyScale(); },
    url: path => cfg.base + path,

    // ---- session ---------------------------------------------------------
    async prepare() {
      if (!page.__demoPrepared) {
        await page.addInitScript(CURSOR);
        page.__demoPrepared = true;
      }
      await page.evaluate(CURSOR).catch(() => {});
      // Drop the built-in action cursor if an earlier session left it on.
      await page.screencast.hideActions().catch(() => {});
    },
    // Full navigation, retried: right after sign-in the first API call of a
    // page load can lose a CSRF race and render "not found".
    async goto(path, { ready, tries = 4 } = {}) {
      for (let i = 0; i < tries; i++) {
        await page.goto(cfg.base + path, { waitUntil: 'load' });
        await page.evaluate(CURSOR).catch(() => {});
        await applyScale();
        try {
          await (ready ? ready() : page.locator('main').waitFor({ timeout: 15000 }));
          const bad = await page.getByText(/not found|do not have access/i).count();
          if (!bad) return;
        } catch (e) { /* retry */ }
        await tick(1500);
      }
      throw new Error('could not load ' + path);
    },
    async idle(ms = 1200, timeout = 15000) {
      // Wait until no request has been in flight for `ms`.
      // Long-polls (socket.io) never finish; they must not hold the scene up.
      const live = r => !/socket\.io|\/api\/method\/frappe\.realtime/.test(r.url());
      let pending = 0, last = Date.now();
      const inc = r => { if (live(r)) { pending++; last = Date.now(); } };
      const dec = r => { if (live(r)) { pending = Math.max(0, pending - 1); last = Date.now(); } };
      page.on('request', inc); page.on('requestfinished', dec); page.on('requestfailed', dec);
      const start = Date.now();
      while (Date.now() - start < timeout) {
        if (pending === 0 && Date.now() - last >= ms) break;
        await tick(150);
      }
      page.off('request', inc); page.off('requestfinished', dec); page.off('requestfailed', dec);
    },

    // ---- recording -------------------------------------------------------
    async start(name, opts = {}) {
      await h.prepare();
      await applyScale();
      slow = opts.slow ?? cfg.slow ?? 1;
      await page.evaluate(s => sessionStorage.setItem('__demo_slow', s), slow);
      await page.mouse.move(pos.x, pos.y);
      await page.screencast.stop().catch(() => {}); // left over from a failed take
      await page.screencast.start({ path: `${cfg.out}/${name}.webm`, size: { width: cfg.width, height: cfg.height }, fps: cfg.fps });
      t0 = Date.now();
      recording = true;
      marks.name = name;
      marks.slow = slow;
      await tick(300);
    },
    // Named timestamps for the editor, in seconds of *playback* time (i.e.
    // already divided by the slow factor).
    now() { return +((Date.now() - t0) / 1000 / slow).toFixed(2); },
    mark(label) { marks[label] = h.now(); },
    async stop() {
      await tick(400);
      marks.end = h.now();
      await page.screencast.stop();
      recording = false;
      slow = 1;
      await page.evaluate(() => sessionStorage.removeItem('__demo_slow')).catch(() => {});
      return marks;
    },

    // ---- pointer ---------------------------------------------------------
    async moveTo(x, y, ms) {
      const sx = pos.x, sy = pos.y;
      const dist = Math.hypot(x - sx, y - sy);
      const dur = (ms ?? Math.min(1100, Math.max(350, dist * 0.9))) * slow;
      // A slight arc reads as a hand, not a robot.
      const bend = Math.min(60, dist * 0.08) * (x > sx ? -1 : 1);
      const nx = dist ? -(y - sy) / dist : 0, ny = dist ? (x - sx) / dist : 0;
      const start = Date.now();
      for (;;) {
        const t = Math.min(1, (Date.now() - start) / dur);
        const e = ease(t), arc = Math.sin(Math.PI * e) * bend;
        pos.x = sx + (x - sx) * e + nx * arc;
        pos.y = sy + (y - sy) * e + ny * arc;
        await page.mouse.move(pos.x, pos.y);
        if (t >= 1) break;
        await tick(8);
      }
    },
    async center(target) {
      const loc = typeof target === 'string' ? page.locator(target) : target;
      await loc.first().scrollIntoViewIfNeeded().catch(() => {});
      const b = await loc.first().boundingBox();
      if (!b) throw new Error('no box for ' + target);
      return { x: b.x + b.width / 2, y: b.y + b.height / 2, box: b };
    },
    async hover(target, ms) {
      const c = await h.center(target);
      await h.moveTo(c.x, c.y, ms);
      return c;
    },
    async click(target, { pause = 260, after = 500 } = {}) {
      if (target) await h.hover(target);
      await sleep(pause);
      // Layout can settle while the pointer travels (a panel header that
      // re-wraps, a list that finishes loading): re-aim before pressing.
      for (let i = 0; target && i < 3; i++) {
        const c = await h.center(target);
        if (Math.hypot(c.x - pos.x, c.y - pos.y) < 3) break;
        await h.moveTo(c.x, c.y, 220);
      }
      await page.mouse.down();
      await tick(90);
      await page.mouse.up();
      await sleep(after);
    },
    async clickAt(x, y, opts) {
      await h.moveTo(x, y);
      await h.click(null, opts);
    },
    // Press-move-release along a path of waypoints, time based.
    async drag(points, ms, { button = 'left', linear = false } = {}) {
      await h.moveTo(points[0][0], points[0][1]);
      await sleep(140);
      await page.mouse.down({ button });
      const start = Date.now();
      const seg = points.length - 1;
      for (;;) {
        const t = Math.min(1, (Date.now() - start) / (ms * slow));
        const e = (linear ? t : ease(t)) * seg;
        const i = Math.min(seg - 1, Math.floor(e)), f = e - i;
        pos.x = points[i][0] + (points[i + 1][0] - points[i][0]) * f;
        pos.y = points[i][1] + (points[i + 1][1] - points[i][1]) * f;
        await page.mouse.move(pos.x, pos.y);
        if (t >= 1) break;
        await tick(8);
      }
      await sleep(120);
      await page.mouse.up({ button });
    },
    // Scroll `total` wheel units over `ms`, in small notches.
    async wheel(total, ms, notch = 40) {
      const n = Math.max(1, Math.round(Math.abs(total) / notch));
      const start = Date.now();
      let sent = 0;
      for (;;) {
        const t = Math.min(1, (Date.now() - start) / (ms * slow));
        const want = Math.round(ease(t) * n);
        if (want > sent) {
          await page.mouse.wheel(0, Math.sign(total) * notch * (want - sent));
          sent = want;
        }
        if (t >= 1) break;
        await tick(12);
      }
    },
    // ---- Leaflet map ---------------------------------------------------
    // The map instance is private to the page component, so screen <-> lat/lng
    // is derived from any XYZ tile on screen (basemap or the task's own tiles).
    map: {
      // The part of the map that is clear of the sidebar, toolbar and layer panel.
      async area() {
        const r = await page.locator('#map').boundingBox();
        return { left: r.x + 40, right: r.x + r.width - 270, top: r.y + 95, bottom: r.y + r.height - 75 };
      },
      async info() {
        return page.evaluate(() => {
          let best = null;
          for (const img of document.querySelectorAll('.leaflet-tile-pane img.leaflet-tile')) {
            const m = img.src.match(/[?&]z=(\d+)&x=(\d+)&y=(\d+)/) || img.src.match(/\/(\d+)\/(\d+)\/(\d+)\.png/);
            if (!m) continue;
            const r = img.getBoundingClientRect();
            if (!r.width) continue;
            if (!best || +m[1] > best.z) best = { z: +m[1], x: +m[2], y: +m[3], left: r.left, top: r.top, s: r.width / 256 };
          }
          return best;
        });
      },
      async project(lat, lng) {
        const b = await h.map.info();
        const n = 256 * 2 ** b.z;
        const wx = (lng + 180) / 360 * n;
        const sin = Math.sin(lat * Math.PI / 180);
        const wy = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * n;
        return { x: b.left + (wx - b.x * 256) * b.s, y: b.top + (wy - b.y * 256) * b.s, zoom: Math.round(b.z + Math.log2(b.s)) };
      },
      async unproject(x, y) {
        const b = await h.map.info();
        const n = 256 * 2 ** b.z;
        const wx = b.x * 256 + (x - b.left) / b.s, wy = b.y * 256 + (y - b.top) / b.s;
        return {
          lat: Math.atan(Math.sinh(Math.PI * (1 - 2 * wy / n))) * 180 / Math.PI,
          lng: wx / n * 360 - 180,
          zoom: Math.round(b.z + Math.log2(b.s)),
        };
      },
      // True once every tile in view has arrived. Leaflet also fetches a
      // margin of tiles around the view; those are not waited for.
      async tilesLoaded(timeout = 45000) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
          const pending = await page.evaluate(() => {
            const map = document.querySelector('#map');
            const pane = document.querySelector('.leaflet-tile-pane');
            if (!map || !pane) return 1;
            const m = map.getBoundingClientRect();
            const inView = img => {
              const r = img.getBoundingClientRect();
              return r.right > m.left && r.left < m.right && r.bottom > m.top && r.top < m.bottom;
            };
            // `complete` is also true for a tile that failed (outside the
            // survey's extent), which Leaflet never marks as loaded.
            return [...pane.querySelectorAll('img.leaflet-tile')].filter(i => !i.complete && inView(i)).length
              + document.querySelectorAll('.leaflet-zoom-anim').length;
          });
          if (!pending) return true;
          await tick(200);
        }
        return false;
      },
      async clamp(p) {
        const a = await h.map.area();
        return { x: Math.min(a.right, Math.max(a.left, p.x)), y: Math.min(a.bottom, Math.max(a.top, p.y)) };
      },
      // One wheel notch per zoom level, with the cursor on the target so the
      // target stays put while the map zooms around it.
      async zoomTo(lat, lng, zoom, { pause = 650 } = {}) {
        for (let i = 0; i < 14; i++) {
          const p = await h.map.project(lat, lng);
          if (p.zoom === zoom) return;
          const c = await h.map.clamp(p);
          if (Math.hypot(c.x - pos.x, c.y - pos.y) > 6) await h.moveTo(c.x, c.y);
          await page.mouse.wheel(0, p.zoom < zoom ? -50 : 50);
          await sleep(pause);
          // On camera the tiles are already cached; do not sit on a slow one.
          await h.map.tilesLoaded(recording ? 900 : 45000);
        }
      },
      // Drag the map so a lat/lng lands on a screen point.
      async panTo(lat, lng, x, y, ms = 1400) {
        const p = await h.map.project(lat, lng);
        const dx = x - p.x, dy = y - p.y;
        if (Math.hypot(dx, dy) < 12) return;
        const a = await h.map.area();
        const sx = Math.min(a.right, Math.max(a.left, (a.left + a.right) / 2 - dx / 2));
        const sy = Math.min(a.bottom, Math.max(a.top, (a.top + a.bottom) / 2 - dy / 2));
        await h.drag([[sx, sy], [sx + dx, sy + dy]], ms);
        await sleep(200);
      },
    },

    // ---- page shortcuts (setup, not performance) -------------------------
    // Start the pointer somewhere sensible before the camera rolls.
    async park(x, y) {
      pos.x = x; pos.y = y;
      await page.mouse.move(x, y);
    },
    // Open a project's map with one task selected and its layers loaded.
    async openTask(project, title, { markers = false } = {}) {
      const card = page.getByText(title, { exact: true }).first();
      await h.goto(`/project/${project}`, { ready: () => card.waitFor({ timeout: 20000 }) });
      await card.click();
      await page.getByRole('checkbox', { name: 'Orthophoto' }).waitFor({ timeout: 30000 });
      await page.getByRole('checkbox', { name: 'Image markers' }).setChecked(markers);
      await h.idle(800);
      await h.map.tilesLoaded();
    },
    layer(name) {
      return page.getByRole('checkbox', { name, exact: typeof name === 'string' });
    },
    async waitText(re, timeout = 30000) {
      await page.waitForFunction(src => new RegExp(src).test(document.querySelector('main')?.innerText || ''), re.source, { timeout });
    },

    async cursor(show) {
      await page.evaluate(s => {
        window.__demoCursorHidden = !s;
        const c = document.getElementById('__demo_cursor');
        if (c) c.style.opacity = s ? '1' : '0';
      }, show);
    },
  };
  return h;
}
