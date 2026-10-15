// Chapter 3 · Visualization — surface model (DSM) and bare-earth terrain (DTM).
async (page, h) => {
  await h.scale(1);
  await h.openTask('Urban', 'Urban - Task 1', { markers: false });
  // Rehearsal: fetch both elevation tile sets once.
  for (const name of ['DSM', 'DTM']) {
    await h.layer(name).check();
    await h.idle(800);
    await h.map.tilesLoaded();
    await h.layer(name).uncheck();
  }
  const slider = name => page.locator('div.py-1', { has: h.layer(name) }).locator('input[type=range]');
  await h.park(1180, 620);

  await h.start('05_elevation');
  h.mark('in');
  await h.sleep(500);
  // The elevation tiles take a moment to render server-side. The edit skips
  // from `dsm_on` to `dsm` (and `dtm_on` to `dtm`), so the layer dissolves in whole.
  await h.click(h.layer('DSM'), { after: 200 });
  h.mark('dsm_on');
  await h.map.tilesLoaded(60000);
  await h.sleep(700);
  h.mark('dsm');
  await h.sleep(1400);
  // Blend the surface model over the orthophoto with the layer's opacity slider.
  const box = await slider('DSM').boundingBox();
  const y = box.y + box.height / 2;
  await h.drag([[box.x + box.width - 8, y], [box.x + box.width * 0.5, y]], 1200);
  h.mark('blend');
  await h.sleep(1100);
  await h.click(h.layer('DSM'), { after: 300 });
  await h.click(h.layer('DTM'), { after: 200 });
  h.mark('dtm_on');
  await h.map.tilesLoaded(60000);
  await h.sleep(700);
  h.mark('dtm');
  await h.moveTo(1150, 640, 700);
  await h.sleep(3000);
  h.mark('out');
  return await h.stop();
}
