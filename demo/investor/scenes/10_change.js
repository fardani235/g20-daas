// Chapter 4 · AI analysis — change detection between two surveys of one site.
// The stored comparison is shown: the layer, the plugin's own summary, and the
// numbers of one region. No new run is started.
async (page, h) => {
  const RUN = 'ofg3cp9sda';
  const overlay = page.getByRole('checkbox', { name: /Change Detection/ });
  await h.scale(1);
  await h.openTask('Beach', 'Beach - Task 1 (copy)', { markers: false });

  // Give the sidebar room for the result summary (the divider is draggable).
  const divider = await page.locator('div.cursor-col-resize').boundingBox();
  await page.mouse.move(divider.x + 3, 520);
  await page.mouse.down();
  await page.mouse.move(540, 520, { steps: 10 });
  await page.mouse.up();
  await h.sleep(600);
  await h.map.tilesLoaded();

  // A point well inside the largest earthwork region, for the click.
  const target = await page.evaluate(async run => {
    const res = await (await fetch('/api/method/webodm_core.api.plugins.get_run_geojson?run_name=' + run)).json();
    const feats = (res.message || res).features.filter(f => ['fill', 'cut'].includes(f.properties.class));
    feats.sort((a, b) => (b.properties.volume_m3 || 0) - (a.properties.volume_m3 || 0));
    const g = feats[0].geometry;
    const rings = g.type === 'Polygon' ? [g.coordinates[0]] : g.coordinates.map(poly => poly[0]);
    const area = r => Math.abs(r.reduce((s, p, i) => s + p[0] * r[(i + 1) % r.length][1] - r[(i + 1) % r.length][0] * p[1], 0));
    const ring = rings.sort((a, b) => area(b) - area(a))[0];
    const inside = (x, y) => {
      let hit = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) hit = !hit;
      }
      return hit;
    };
    const xs = ring.map(p => p[0]), ys = ring.map(p => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    let best = null;
    for (let i = 1; i < 30; i++) for (let j = 1; j < 30; j++) {
      const x = x0 + (x1 - x0) * i / 30, y = y0 + (y1 - y0) * j / 30;
      if (!inside(x, y)) continue;
      const d = Math.min(...ring.map(p => Math.hypot(p[0] - x, (p[1] - y) * 1.46)));
      if (!best || d > best.d) best = { d, lng: x, lat: y };
    }
    return best;
  }, RUN);

  // Warm the summary, then fold it again for the take.
  const toggle = page.locator('[data-run-summary-toggle]');
  await toggle.click();
  await page.locator('[data-run-summary]').waitFor();
  await toggle.click();
  await h.park(1180, 640);

  await h.start('10_change');
  h.mark('in');
  await h.sleep(600);
  await h.click(overlay, { after: 1500 });
  h.mark('layer');
  await h.click(toggle, { after: 500 });
  h.mark('results');
  await h.hover(page.locator('[data-run-summary]').getByText('Changed', { exact: true }), 800);
  await h.sleep(1200);
  await h.hover(page.locator('[data-run-summary]').getByText('Net', { exact: true }), 700);
  await h.sleep(1800);
  h.mark('region');
  const p = await h.map.project(target.lat, target.lng);
  for (let k = 0; k < 3 && !(await page.locator('.leaflet-popup-content').count()); k++) {
    await h.clickAt(p.x, p.y + k * 2, { after: 500 });
  }
  h.mark('popup');
  await h.moveTo(p.x + 150, p.y + 120, 800);
  await h.sleep(2600);
  h.mark('out');
  return await h.stop();
}
