// Chapter 5 · Actionable insights — every output is a standard file to download.
// The download buttons are pointed at, not pressed.
async (page, h) => {
  await h.scale(1.5);
  await h.goto('/project/Urban/task/b4m1p5tjj5/console', { ready: () => page.getByText('RASTER METADATA').waitFor() });
  await h.idle();
  await page.getByText('RASTER METADATA').click();
  await page.getByText('Pixel size').first().waitFor();
  await h.park(1250, 330);

  await h.start('11_deliverables', { slow: 2 });
  h.mark('in');
  await h.sleep(500);
  for (const name of ['Orthophoto', 'DSM', 'DTM', 'Point Cloud', '3D Model']) {
    await h.hover(page.getByRole('link', { name, exact: true }), 480);
    await h.sleep(330);
  }
  h.mark('metadata');
  await h.hover(page.getByText('Pixel size').first(), 800);
  await h.sleep(900);
  await h.hover(page.getByText('Layout').first(), 600);
  await h.sleep(1900);
  h.mark('out');
  return await h.stop();
}
