// Chapter 3 · Visualization — the textured 3D model: zoom, rotate, pan.
async (page, h) => {
  await h.scale(1);
  await h.goto('/project/Urban/task/b4m1p5tjj5/model', {
    ready: () => page.getByText(/tris/).waitFor({ timeout: 90000 }),
  });
  await h.sleep(2500);
  await h.park(960, 610);

  await h.start('07_model3d');
  h.mark('in');
  await h.sleep(600);
  // Scroll in on the middle of the site until it fills the stage.
  await h.wheel(-1900, 2300, 25);
  await h.sleep(250);
  h.mark('rotate');
  await h.drag([[760, 620], [1180, 600]], 3000);
  await h.sleep(300);
  h.mark('closer');
  await h.moveTo(960, 640, 450);
  await h.wheel(-700, 1300, 25);
  h.mark('pan');
  await h.drag([[1000, 600], [920, 640]], 1400, { button: 'right' });
  await h.sleep(200);
  h.mark('orbit');
  await h.drag([[1080, 600], [880, 632]], 2700);
  await h.sleep(1100);
  h.mark('out');
  return await h.stop();
}
