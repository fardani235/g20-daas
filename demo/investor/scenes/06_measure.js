// Chapter 3 · Visualization — measure a structure: footprint area and volume
// against the surface model. A read-only computation; nothing is stored.
async (page, h) => {
  const BARN = { lat: 46.549911, lng: 6.679398 };
  const CORNERS = [
    [46.549950, 6.678861], [46.550171, 6.679781], [46.549857, 6.679934], [46.549651, 6.679017],
  ];
  await h.scale(1);
  await h.openTask('Urban', 'Urban - Task 1', { markers: false });
  await h.map.zoomTo(BARN.lat, BARN.lng, 19);
  await h.map.panTo(BARN.lat, BARN.lng, 1030, 600, 300);
  await h.map.tilesLoaded();
  await h.park(1300, 330);

  await h.start('06_measure');
  h.mark('in');
  await h.sleep(500);
  await h.click(page.getByRole('button', { name: 'Volume' }), { after: 600 });
  h.mark('draw');
  for (const [lat, lng] of CORNERS) {
    const p = await h.map.project(lat, lng);
    await h.clickAt(p.x, p.y, { pause: 220, after: 380 });
  }
  await h.click(page.getByRole('button', { name: 'Finish' }), { after: 200 });
  await h.waitText(/m³/);
  h.mark('result');
  await h.moveTo(1180, 250, 900);
  await h.sleep(2800);
  h.mark('out');
  const marks = await h.stop();
  await page.getByRole('button', { name: 'Clear measurement' }).click();
  return marks;
}
