// Chapter 3 · Visualization — the orthophoto on the map: camera positions,
// flight path, satellite context, then down to pixel level.
async (page, h) => {
  const BARN = { lat: 46.549911, lng: 6.679398 };
  await h.scale(1);

  // Rehearsal: walk the same path once so every tile is in the browser cache.
  await h.openTask('Urban', 'Urban - Task 1', { markers: true });
  await page.getByRole('radio', { name: 'Satellite' }).check();
  await h.map.tilesLoaded();
  await h.map.panTo(BARN.lat, BARN.lng, 1030, 600, 300);
  await h.map.zoomTo(BARN.lat, BARN.lng, 20);
  await h.map.tilesLoaded();

  const card = page.getByText('Urban - Task 1', { exact: true }).first();
  await h.goto('/project/Urban', { ready: () => card.waitFor() });
  await h.idle(800);
  await h.map.tilesLoaded();
  await h.park(820, 470);

  await h.start('04_map');
  h.mark('in');
  await h.sleep(500);
  await h.click(card, { after: 200 });
  await page.getByRole('checkbox', { name: 'Orthophoto' }).waitFor();
  await h.map.tilesLoaded(15000);
  h.mark('task');
  await h.sleep(1300);
  await h.click(h.layer('Flight path'), { after: 1000 });
  h.mark('path');
  await h.click(h.layer('Image markers'), { after: 400 });
  await h.click(page.getByRole('radio', { name: 'Satellite' }), { after: 300 });
  await h.map.tilesLoaded(15000);
  h.mark('satellite');
  await h.sleep(900);
  h.mark('zoom');
  // Centre the building at site scale, then scroll straight down onto it.
  await h.map.panTo(BARN.lat, BARN.lng, 1030, 600, 900);
  await h.map.zoomTo(BARN.lat, BARN.lng, 20, { pause: 380 });
  // The full-resolution tiles take a few seconds to arrive. The edit skips
  // from `zoomed` to `detail`, so the wait is recorded but not watched.
  h.mark('zoomed');
  await h.map.tilesLoaded(90000);
  await h.sleep(300);
  h.mark('detail');
  await h.sleep(2300);
  h.mark('out');
  return await h.stop();
}
