#!/usr/bin/env node
// Reads state attorney general breach lists that a plain page fetch cannot,
// and prints rows reported on or after a date as JSON.
// Usage: node fqhc-breach-watch/state_lists.js <since YYYY-MM-DD> [tx,or,in,wi]
//   tx  Texas AG      (script-filled table; headless Chromium)
//   or  Oregon DOJ    (HTML table)
//   in  Indiana AG    (year-to-date PDF linked from the AG page; needs pdftotext)
//   wi  Wisconsin DATCP (text blocks, current calendar year)
// Each row: { source, entity, reported, breach_dates, state_residents, total, extra }
// A source that fails is listed under "failed" and the others still print.
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36';
const SRC = {
  tx: 'https://oag.my.site.com/datasecuritybreachreport/apex/DataSecurityReportsPage',
  or: 'https://justice.oregon.gov/consumer/DataBreach/',
  in: 'https://www.in.gov/attorneygeneral/consumer-protection-division/id-theft-prevention/security-breaches/',
  wi: 'https://datcp.wi.gov/Pages/Programs_Services/DataBreaches.aspx',
};

const curl = (url, out) => execFileSync('curl', ['-sSL', '--fail', '-m', '90', '-A', UA, ...(out ? ['-o', out] : []), url],
  { maxBuffer: 64 * 1024 * 1024, encoding: out ? undefined : 'utf8' });
const clean = s => s.replace(/<[^>]+>/g, ' ').replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/[\u200b\u00a0]/g, ' ').replace(/&amp;/g, '&').replace(/&#0?39;|&apos;|&rsquo;/g, "'")
  .replace(/&quot;/g, '"').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();
// "11/9/2026" -> "2026-11-09"; returns '' when no date is present
const iso = s => { const m = /(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(s || ''); return m ? `${m[3]}-${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}` : ''; };
const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const isoLong = s => { const m = /([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})/.exec(s || ''); if (!m) return iso(s); const i = MONTHS.findIndex(x => x.startsWith(m[1].toLowerCase().slice(0, 3))); return i < 0 ? '' : `${m[3]}-${String(i + 1).padStart(2, '0')}-${m[2].padStart(2, '0')}`; };

async function texas() {
  const { chromium } = require('playwright');
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ userAgent: UA })).newPage();
    await page.goto(SRC.tx, { waitUntil: 'networkidle', timeout: 70000 }).catch(() => {});
    await page.waitForTimeout(5000);
    const data = await page.evaluate(() => {
      const d = window.jQuery('table').first().DataTable().rows().data().toArray();
      return d.map(x => (Array.isArray(x) ? x : Object.values(x)).map(String));
    });
    if (!data.length) throw new Error('no rows in table');
    return data.map(x => ({ source: 'TX AG', entity: clean(x[0]), reported: iso(x[9]), breach_dates: null,
      state_residents: clean(x[6]), total: null, extra: `${clean(x[2])}, ${clean(x[3])}; ${clean(x[5])}` }));
  } finally { await browser.close(); }
}

function oregon() {
  const html = curl(SRC.or);
  const rows = [];
  for (const m of html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)) {
    const c = [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(x => clean(x[1]));
    if (c.length < 6 || !iso(c[1])) continue;
    rows.push({ source: 'OR DOJ', entity: c[0], reported: iso(c[1]), breach_dates: c[2], state_residents: c[5], total: null, extra: `notice sent ${c[4]}` });
  }
  if (!rows.length) throw new Error('no rows parsed');
  return rows;
}

function indiana() {
  const index = curl(SRC.in);
  const year = String(new Date().getUTCFullYear());
  const link = [...index.matchAll(/href="([^"]+\.pdf)"[^>]*>\s*(\d{4})\s*</g)].find(m => m[2] === year);
  if (!link) throw new Error(`no ${year} PDF link on the AG page`);
  const pdf = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'inag-')), 'in.pdf');
  curl(new URL(link[1], SRC.in).href, pdf);
  const text = execFileSync('pdftotext', ['-layout', pdf, '-'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  const rows = [];
  for (const line of text.split('\n')) {
    const m = /^\s*\d+\s+(.+?)\s{2,}(\d{1,2}\/\d{1,2}\/\d{4})\s+(\d{1,2}\/\d{1,2}\/\d{4})(?:\s+([\d,]+))?(?:\s+([\d,]+))?\s*$/.exec(line);
    if (!m) continue;
    rows.push({ source: 'IN AG', entity: m[1].trim(), reported: iso(m[2]), breach_dates: m[3], state_residents: m[4] || null, total: m[5] || null, extra: 'reported = date notification sent' });
  }
  if (!rows.length) throw new Error('no rows parsed from PDF');
  return rows;
}

function wisconsin() {
  const text = clean(curl(SRC.wi));
  const rows = [];
  for (const b of text.split(/Company Name:/).slice(1)) {
    const f = k => (new RegExp(k + ':\\s*(.*?)(?=\\s+(?:Date of Incident|Date Public Notified|Data Accessed|Who is Affected|Number of Individuals Affected|Number of Wisconsin Residents Affected|Who and how to contact)\\b|$)').exec(b) || [])[1] || null;
    const entity = (/^(.*?)\s+Date of Incident:/.exec(b) || [])[1];
    if (!entity) continue;
    rows.push({ source: 'WI DATCP', entity: entity.trim(), reported: isoLong(f('Date Public Notified')), breach_dates: f('Date of Incident'),
      state_residents: f('Number of Wisconsin Residents Affected'), total: f('Number of Individuals Affected'), extra: null });
  }
  if (!rows.length) throw new Error('no entries parsed');
  return rows;
}

(async () => {
  const since = process.argv[2];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(since || '')) { console.error('usage: state_lists.js <since YYYY-MM-DD> [tx,or,in,wi]'); process.exit(2); }
  const want = (process.argv[3] || 'tx,or,in,wi').split(',');
  const readers = { tx: texas, or: oregon, in: indiana, wi: wisconsin };
  const out = { since, fetched_at: new Date().toISOString(), newest: {}, failed: {}, rows: [] };
  for (const k of want) {
    try {
      const rows = await readers[k]();
      out.newest[k] = rows.map(r => r.reported).filter(Boolean).sort().pop() || null;
      out.rows.push(...rows.filter(r => r.reported >= since));
    } catch (e) { out.failed[k] = String(e.message || e).slice(0, 200); }
  }
  out.rows.sort((a, b) => b.reported.localeCompare(a.reported));
  console.log(JSON.stringify(out, null, 1));
})();
