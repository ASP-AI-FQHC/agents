// jwpub-mail-check: log in to mail.jwpub.org (Outlook Web Access behind login.jw.org SSO)
// and dump the unread inbox to out.json. Run from this folder after `npm install`.
// Env: JWPUB_USER (default gfuller@jwpub.org), JWPUB_PASSWORD (required).
// Two-step verification: when the site asks for the emailed six-digit code, this script
// writes otp-requested.flag and waits (up to 9 minutes) for the agent to write the code
// into code.txt. If the code is rejected it requests a new one (max 3 tries).
// Exit codes: 0 ok, 2 no code arrived, 3 login failed, 4 could not read inbox.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const here = __dirname;
const f = (n) => path.join(here, n);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';

(async () => {
  const user = process.env.JWPUB_USER || 'gfuller@jwpub.org';
  const pass = process.env.JWPUB_PASSWORD;
  if (!pass) { console.error('JWPUB_PASSWORD not set'); process.exit(3); }
  for (const n of ['code.txt', 'otp-requested.flag', 'out.json']) fs.rmSync(f(n), { force: true });

  const PRESET_CHROMIUM = '/opt/pw-browsers/chromium';
  const browser = await chromium.launch({
    headless: true,
    ...(fs.existsSync(PRESET_CHROMIUM) ? { executablePath: PRESET_CHROMIUM } : {}),
  });
  const ctx = await browser.newContext({ userAgent: UA, viewport: { width: 1400, height: 1400 } });
  const page = await ctx.newPage();
  const settle = async () => { await page.waitForLoadState('networkidle').catch(() => {}); await page.waitForTimeout(3000); };
  await page.goto('https://mail.jwpub.org/owa/', { waitUntil: 'networkidle', timeout: 90000 }).catch(e => log('goto', e.message));
  log('start', page.url());

  let codeTries = 0;
  for (let step = 0; step < 25; step++) {
    const url = page.url();
    const body = await page.innerText('body').catch(() => '');
    if (url.includes('mail.jwpub.org') && !url.includes('login') && !body.includes('Please update your browser')) break;
    if (url.includes('login.jw.org/username')) {
      const accept = page.getByRole('button', { name: 'Accept' }); if (await accept.count()) await accept.first().click();
      await page.fill('#username', user); await page.click('#submit-button');
    } else if (url.includes('login.jw.org/password')) {
      await page.locator('input[type=password]').first().fill(pass); await page.keyboard.press('Enter');
      await settle();
      if (page.url().includes('login.jw.org/password')) { log('password rejected'); await page.screenshot({ path: f('fail.png') }); process.exit(3); }
    } else if (url.includes('stay-logged-in')) {
      await page.getByRole('button', { name: 'Yes' }).first().click();
    } else if (url.includes('challenge/otp')) {
      await page.getByText('Send a Code by Email').first().click();
    } else if (url.includes('challenge/email') && body.includes('Where should the code be sent')) {
      await page.getByRole('button', { name: 'Send Code' }).first().click();
    } else if (url.includes('challenge/email') && (body.includes('no longer valid') || body.includes('Send New Code'))) {
      if (++codeTries >= 3) { log('code rejected too many times'); process.exit(2); }
      fs.rmSync(f('code.txt'), { force: true });
      await page.getByRole('button', { name: 'Send New Code' }).first().click();
    } else if (url.includes('challenge/email') && body.includes('Enter the six-digit code')) {
      fs.writeFileSync(f('otp-requested.flag'), new Date().toISOString());
      log('OTP requested; waiting for code.txt');
      let code = null;
      for (let i = 0; i < 270; i++) {
        if (fs.existsSync(f('code.txt'))) { code = fs.readFileSync(f('code.txt'), 'utf8').trim(); if (/^\d{6}$/.test(code)) break; code = null; }
        await page.waitForTimeout(2000);
      }
      if (!code) { log('no code received'); process.exit(2); }
      fs.rmSync(f('otp-requested.flag'), { force: true });
      await page.locator('input[type=text], input[type=tel], input[inputmode=numeric]').first().fill(code);
      await page.keyboard.press('Enter');
    } else if (body.includes('You Are Now Logged In')) {
      await page.getByRole('button', { name: 'Continue' }).first().click();
    } else if (body.includes('Please update your browser')) {
      await page.goto('https://mail.jwpub.org/owa/#path=/mail', { waitUntil: 'networkidle', timeout: 90000 }).catch(() => {});
    } else {
      log('unknown page', url, body.slice(0, 300).replace(/\s+/g, ' '));
      await page.screenshot({ path: f(`unknown-${step}.png`) });
    }
    await settle();
    log('step', step, '->', page.url());
  }
  if (!page.url().includes('mail.jwpub.org')) { log('never reached OWA'); await page.screenshot({ path: f('fail.png') }); process.exit(3); }

  await page.waitForTimeout(10000);
  const nav = await page.innerText('body').catch(() => '');
  const m = nav.match(/Inbox\s*\n?\s*(\d+)/);
  const unreadTotal = m ? parseInt(m[1], 10) : null;

  const filter = page.getByRole('button', { name: /^Filter/ });
  if (await filter.count()) {
    await filter.first().click(); await page.waitForTimeout(1500);
    const unread = page.getByRole('menuitem', { name: /^Unread/ }).or(page.getByText('Unread', { exact: true }));
    if (await unread.count()) { await unread.first().click(); await page.waitForTimeout(6000); }
  }
  for (let i = 0; i < 10; i++) { await page.mouse.wheel(0, 1500); await page.waitForTimeout(700); }
  await page.screenshot({ path: f('inbox.png') });
  const raw = await page.$$eval('[role="option"], [role="listitem"]', els => els.map(e => e.innerText.trim()).filter(Boolean));
  const items = raw.map(t => {
    const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
    const sender = lines[0] || ''; let subject = lines[1] || ''; let i = 2; let count = 1;
    if (/^\(\d+\)$/.test(lines[i] || '')) { count = parseInt(lines[i].slice(1, -1), 10); i++; }
    const date = lines[i] || ''; const snippet = lines.slice(i + 1).join(' ').slice(0, 240);
    return { sender, subject, count, date, snippet };
  });
  if (!items.length && unreadTotal) { log('inbox list empty'); process.exit(4); }
  fs.writeFileSync(f('out.json'), JSON.stringify({ checkedAt: new Date().toISOString(), unreadTotal, items }, null, 2));
  log('done', items.length, 'unread conversations; inbox badge', unreadTotal);
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(4); });
