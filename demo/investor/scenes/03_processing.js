// Chapter 2 · Processing — a completed photogrammetry task and the task form.
// Nothing is submitted: the dialog is opened, shown and cancelled.
async (page, h) => {
  await h.scale(1);
  await h.openTask('Urban', 'Urban - Task 1', { markers: true });
  // Warm the dialog (dataset list, presets, ODM option catalog).
  await page.getByRole('button', { name: 'Add Task' }).click();
  await page.locator('#upload-preset option').nth(3).waitFor({ state: 'attached' });
  await page.getByText('feature-quality').waitFor();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await h.sleep(400);
  await h.park(760, 560);

  await h.start('03_processing', { slow: 2 });
  h.mark('in');
  await h.sleep(600);
  await h.hover(page.getByText('Completed', { exact: true }).first(), 700);
  await h.sleep(500);
  await h.hover(page.getByText(/14,740/), 600);
  await h.sleep(1000);
  h.mark('add');
  await h.click(page.getByRole('button', { name: 'Add Task' }), { after: 400 });
  h.mark('dialog');
  await h.click(page.getByRole('tab', { name: 'Upload new images' }), { after: 500 });
  await h.hover(page.getByText('Drop images here'), 600);
  await h.sleep(900);
  await h.hover(page.locator('#upload-preset'), 700);
  await h.sleep(600);
  await h.hover(page.getByText('feature-quality'), 700);
  await h.wheel(240, 1200, 16);
  await h.sleep(800);
  h.mark('cancel');
  await h.click(page.getByRole('button', { name: 'Cancel' }), { after: 600 });
  h.mark('out');
  return await h.stop();
}
