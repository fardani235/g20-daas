// Chapter 4 · AI analysis — object detection on the orthophoto.
// The completed run already stored with the task is shown; the run dialog is
// opened to show the detector choice and cancelled, so no new run is started.
async (page, h) => {
  // A parking row inside the densest group of detections that is not on the
  // survey's edge.
  const ROW = { lat: 46.551145, lng: 6.681632 };
  const overlay = h.layer('Object Detection');
  const runButton = page.getByRole('button', { name: 'Object Detection' });
  await h.scale(1);

  // Rehearsal for the tile cache.
  await h.openTask('Urban', 'Urban - Task 1', { markers: false });
  await h.map.panTo(ROW.lat, ROW.lng, 1030, 600, 300);
  await h.map.zoomTo(ROW.lat, ROW.lng, 21);
  await h.map.tilesLoaded();
  const model = page.getByRole('dialog').locator('select').first();
  await runButton.click();
  await model.waitFor();
  await page.getByRole('button', { name: 'Cancel' }).click();

  await h.openTask('Urban', 'Urban - Task 1', { markers: false });
  await h.park(900, 560);

  await h.start('09_detect');
  h.mark('in');
  await h.sleep(700);
  await h.click(runButton, { after: 700 });
  h.mark('dialog');
  // The detector that produced the stored result.
  await h.hover(model, 700);
  await h.sleep(300);
  await model.selectOption('visdrone-yolov11s');
  await h.sleep(1300);
  await h.hover(page.getByText('Confidence threshold'), 600);
  await h.sleep(900);
  h.mark('dialog_out');
  await h.click(page.getByRole('button', { name: 'Cancel' }), { after: 600 });
  h.mark('layer');
  await h.click(overlay, { after: 2800 });  // long enough for the edit to push in on the legend
  h.mark('zoom');
  // Centre the car park at site scale, then scroll straight down onto it.
  await h.map.panTo(ROW.lat, ROW.lng, 1030, 600, 900);
  await h.map.zoomTo(ROW.lat, ROW.lng, 21, { pause: 380 });
  // As in 04_map: the edit skips the wait for full-resolution tiles.
  h.mark('zoomed');
  await h.map.tilesLoaded(90000);
  await h.sleep(300);
  h.mark('detail');
  await h.sleep(3000);
  h.mark('out');
  return await h.stop();
}
