// Chapter 5 · Actionable insights — analysis is pluggable: installed plugins
// and the marketplace they come from. Browsing only; nothing is installed.
async (page, h) => {
  await h.scale(1.25);
  await h.goto('/marketplace', { ready: () => page.getByText('Object Detection Plugin').waitFor() });
  await h.idle();
  await h.goto('/plugins', { ready: () => page.getByRole('heading', { name: 'Analysis plugins' }).waitFor() });
  await page.getByText('Semantic Segmentation').first().waitFor();
  await h.idle();
  await h.park(1100, 680);

  await h.start('12_ecosystem', { slow: 2 });
  h.mark('in');
  await h.sleep(500);
  await h.hover(page.getByText('Object Detection', { exact: true }).first(), 800);
  await h.sleep(800);
  await h.hover(page.getByText('Change Detection', { exact: true }).first(), 700);
  await h.sleep(800);
  await h.hover(page.getByText('Semantic Segmentation', { exact: true }).first(), 700);
  await h.sleep(500);
  h.mark('to_market');
  await h.click(page.getByRole('main').getByRole('button', { name: 'Marketplace' }), { after: 300 });
  await page.getByText('Object Detection Plugin').waitFor();
  h.mark('market');
  await h.sleep(700);
  await h.hover(page.getByText('Change Detection Plugin'), 800);
  await h.sleep(700);
  await h.hover(page.getByText('Object Detection Plugin'), 700);
  await h.sleep(1400);
  h.mark('out');
  return await h.stop();
}
