#!/usr/bin/env node
// Reads the HHS OCR breach portal ("under investigation" list, newest first)
// with the pre-installed headless Chromium and prints rows as JSON.
// Usage: node fqhc-breach-watch/ocr_portal.js [pages]   (100 rows per page, default 2)
// The portal's own scripts break Playwright selectors, so this uses
// page.evaluate and parses the HTML itself.
const { chromium } = require('playwright');

const URL = 'https://ocrportal.hhs.gov/ocr/breach/breach_report.jsf';
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36';
const FIELDS = ['entity', 'state', 'entity_type', 'individuals_affected',
  'submission_date', 'breach_type', 'location', 'business_associate_present'];

const clean = s => s.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&#39;|&apos;/g, "'")
  .replace(/&quot;/g, '"').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();

function parse(html) {
  const rows = [];
  for (const m of html.matchAll(/<tr[^>]*data-ri="\d+"[^>]*>([\s\S]*?)<\/tr>/g)) {
    const cells = [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(c => clean(c[1]));
    if (cells.length < 9) continue;
    const row = {};
    FIELDS.forEach((f, i) => { row[f] = cells[i + 1]; });
    rows.push(row);
  }
  const total = (html.match(/Displaying [\d,]+ - [\d,]+ of ([\d,]+)/) || [])[1] || null;
  return { rows, total };
}

(async () => {
  const pages = Math.max(1, parseInt(process.argv[2] || '2', 10));
  const browser = await chromium.launch();
  const out = { source: URL, fetched_at: new Date().toISOString(), total_listed: null, rows: [] };
  try {
    const page = await (await browser.newContext({ userAgent: UA })).newPage();
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
    const ok = await page.evaluate(() => {
      const a = [...document.querySelectorAll('a')].find(x => x.textContent.trim() === 'View HIPAA Breach Reports');
      if (a) a.click();
      return !!a;
    });
    if (!ok) throw new Error('"View HIPAA Breach Reports" link not found; portal layout changed');
    await page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {});
    await page.waitForTimeout(4000);
    for (let i = 0; i < pages; i++) {
      const { rows, total } = parse(await page.content());
      if (!rows.length) { if (i === 0) throw new Error('no table rows found'); break; }
      out.total_listed = total;
      out.rows.push(...rows);
      if (i === pages - 1) break;
      const first = rows[0].entity + rows[0].submission_date;
      const more = await page.evaluate(() => {
        const n = document.querySelector('.ui-paginator-next:not(.ui-state-disabled)');
        if (n) n.click();
        return !!n;
      });
      if (!more) break;
      for (let t = 0; t < 30; t++) {
        await page.waitForTimeout(1000);
        const r = parse(await page.content()).rows;
        if (r.length && r[0].entity + r[0].submission_date !== first) break;
      }
    }
    out.newest_submission_date = out.rows.length ? out.rows[0].submission_date : null;
    console.log(JSON.stringify(out, null, 1));
  } catch (e) {
    console.error('OCR portal read failed: ' + e.message);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
