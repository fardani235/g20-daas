// Chapter 1 · Drone data — one dataset: its geotagged source images.
async (page, h) => {
  await h.scale(1.5);
  await h.goto('/datasets/ipoqneane8', { ready: () => page.getByRole('heading', { name: 'Cadastre' }).waitFor() });
  await page.locator('main img').nth(20).waitFor();
  // Redaction: the "Created by" card prints the signed-in account's e-mail.
  await page.evaluate(() => {
    for (const el of document.querySelectorAll('main *')) {
      if (el.children.length === 0 && /\S+@\S+/.test(el.textContent || '')) el.style.filter = 'blur(9px)';
    }
  });
  // Warm every thumbnail the scroll will pass, then return to the top.
  await h.park(960, 700);
  await page.mouse.wheel(0, 1400);
  await h.idle(1000);
  await page.mouse.wheel(0, -3000);
  await h.idle(600);

  await h.start('02_dataset', { slow: 2 });
  h.mark('in');
  await h.sleep(1000);
  await h.hover(page.getByText('240 MB'), 900);
  await h.sleep(900);
  await h.moveTo(980, 760, 800);
  h.mark('scroll');
  await h.wheel(640, 2800, 16);
  await h.sleep(500);
  await h.hover(page.locator('main img').nth(11), 900);
  await h.sleep(1600);
  h.mark('out');
  return await h.stop();
}
