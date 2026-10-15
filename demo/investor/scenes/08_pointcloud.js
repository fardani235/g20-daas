// Chapter 3 · Visualization — the point cloud: colour by elevation and by
// class, then an elevation profile across the site.
async (page, h) => {
  // Transect ends, as screen points on the framed cloud (see `frame` below).
  const P1 = [760, 590], P2 = [1210, 705];
  const mode = name => page.getByRole('radio', { name, exact: true });
  const settled = () => page.waitForFunction(
    () => !/loading/.test(document.querySelector('[aria-label="Point cloud settings"]')?.innerText || 'loading'),
    null, { timeout: 60000 }).catch(() => {});

  await h.scale(1);
  await h.goto('/project/Urban/task/b4m1p5tjj5/model?source=pointcloud', {
    ready: () => page.getByRole('region', { name: 'Point cloud settings' }).waitFor({ timeout: 90000 }),
  });
  await settled();
  // Frame: closer than the default view. Done before recording so the octree
  // nodes for this view (and for each colour mode) are already loaded.
  await h.park(960, 640);
  await h.wheel(-900, 900, 30);
  await h.sleep(1500);
  await settled();
  for (const name of ['Elevation', 'Classification', 'RGB']) {
    await mode(name).click();
    await h.sleep(1200);
    await settled();
  }

  await h.start('08_pointcloud', { slow: 4 });
  h.mark('in');
  await h.sleep(900);
  await h.drag([[820, 660], [1120, 620]], 2500);
  await h.sleep(300);
  h.mark('elevation');
  await h.click(mode('Elevation'), { after: 1600 });
  h.mark('classes');
  await h.click(mode('Classification'), { after: 2000 });
  await h.click(mode('RGB'), { after: 400 });
  h.mark('profile_tool');
  await h.click(page.getByRole('radio', { name: 'Measure elevation profile' }), { after: 400 });
  // A click only counts when down and up land within 500 ms; under load one
  // can be dropped, so confirm each vertex from the on-screen hint.
  const vertices = async () => {
    const t = await page.locator('main').innerText();
    if (/click 2 more points/.test(t)) return 0;
    if (/click 1 more point/.test(t)) return 1;
    return /compute the profile/.test(t) ? 2 : 0;
  };
  for (const [i, [x, y]] of [P1, P2].entries()) {
    for (let k = 0; k < 4 && (await vertices()) <= i; k++) {
      await h.clickAt(x, y, { pause: 200, after: 350 });
    }
  }
  await page.keyboard.press('Enter');
  await page.waitForFunction(
    () => /Profile ·/.test(document.querySelector('[data-profile-title]')?.innerText || ''),
    null, { timeout: 60000 });
  h.mark('profile');
  await h.sleep(700);
  // Read along the chart: the hovered station is echoed in the 3D view.
  // (the panel's largest svg is the chart; the others are icons)
  const boxes = await page.locator('[data-profile-panel] svg').evaluateAll(els => els.map(e => {
    const r = e.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  }));
  const chart = boxes.sort((a, b) => b.width * b.height - a.width * a.height)[0];
  const cy = chart.y + chart.height * 0.55;
  await h.moveTo(chart.x + chart.width * 0.12, cy, 800);
  await h.moveTo(chart.x + chart.width * 0.62, cy, 2000);
  await h.sleep(1000);
  h.mark('out');
  const marks = await h.stop();
  await page.locator('[data-profile-panel] header button').last().click().catch(() => {});
  return marks;
}
