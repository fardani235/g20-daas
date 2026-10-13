// Chapter 1 · Drone data — the organization workspace, then the dataset library.
async (page, h) => {
  await h.scale(1.5);
  // Warm both pages so the recorded navigation has nothing left to fetch.
  await h.goto('/datasets', { ready: () => page.getByRole('heading', { name: 'Datasets' }).waitFor() });
  await h.idle();
  await h.goto('/dashboard', { ready: () => page.getByRole('heading', { name: 'Dashboard' }).waitFor() });
  await h.idle();
  await h.park(1300, 720);

  await h.start('01_workspace', { slow: 2 });
  h.mark('dashboard');
  await h.sleep(1200);
  await h.hover(page.getByText('Total Tasks'), 1000);
  await h.sleep(500);
  await h.hover(page.getByText('Completed', { exact: true }).first(), 700);
  await h.sleep(800);
  await h.hover(page.getByRole('button', { name: /^Urban/ }), 900);
  await h.sleep(1000);
  h.mark('to_datasets');
  await h.click(page.getByRole('link', { name: 'Datasets' }), { after: 300 });
  await page.getByRole('heading', { name: 'Datasets' }).waitFor();
  await page.getByRole('cell', { name: 'Cadastre', exact: true }).waitFor();
  h.mark('datasets');
  await h.sleep(1000);
  await h.hover(page.getByRole('cell', { name: '87 images' }), 900);
  await h.sleep(600);
  await h.hover(page.getByRole('cell', { name: 'Cadastre', exact: true }), 900);
  await h.sleep(1800);
  h.mark('out');
  return await h.stop();
}
