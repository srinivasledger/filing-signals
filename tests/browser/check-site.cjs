#!/usr/bin/env node
// Real-browser release check. By default, serve the built public/ directory.
// BASE_URL checks a live site; VERIFY_DEPLOYED_BUILD=1 also waits for its stamp
// to match public/index.html. EXPECTED_BUILD and DATA_CUTOFF can override those
// two stamp fields explicitly. Failure evidence goes to artifacts/browser-check.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const publicDir = path.resolve(process.env.PUBLIC_DIR || path.join(root, 'public'));
const artifacts = path.resolve(process.env.BROWSER_ARTIFACT_DIR || path.join(root, 'artifacts/browser-check'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const diagnostics = {phase: process.env.CHECK_PHASE || 'manual', step: 'setup',
  pageErrors: [], consoleErrors: [], assetFailures: []};
const engine = process.env.BROWSER_ENGINE || 'chromium';
diagnostics.browser = engine;
const monitored = new WeakSet();
let browser;
let server;
let activePage;
let timeout;

async function serve() {
  await fs.access(path.join(publicDir, 'index.html'));
  const types = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript',
    '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
    '.ico': 'image/x-icon', '.xml': 'application/xml', '.txt': 'text/plain',
    '.woff2': 'font/woff2'};
  server = http.createServer(async (request, response) => {
    try {
      if (!['GET', 'HEAD'].includes(request.method)) {
        response.writeHead(405).end();
        return;
      }
      let pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (pathname.endsWith('/')) pathname += 'index.html';
      const filename = path.resolve(publicDir, '.' + pathname);
      if (!filename.startsWith(publicDir + path.sep) || filename.includes('\0')) {
        response.writeHead(400).end();
        return;
      }
      const body = await fs.readFile(filename);
      response.writeHead(200, {'Content-Type': types[path.extname(filename)] || 'application/octet-stream',
        'Cache-Control': 'no-store'});
      response.end(request.method === 'HEAD' ? undefined : body);
    } catch (error) {
      response.writeHead(error.code === 'ENOENT' || error.code === 'EISDIR' ? 404 : 500).end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return `http://127.0.0.1:${server.address().port}/index.html`;
}

function homepage(base) {
  const url = new URL(base);
  assert(['http:', 'https:'].includes(url.protocol), 'BASE_URL must use HTTP(S)');
  if (!url.pathname.endsWith('.html')) {
    url.pathname = url.pathname.replace(/\/?$/, '/') + 'index.html';
  }
  url.hash = '';
  return url.href;
}

async function stamp(page) {
  return page.locator('html').evaluate(node => ({
    built: node.dataset.built || '', through: node.dataset.through || '',
    events: node.dataset.events || '',
  }));
}

async function expectedStamp() {
  const expected = {};
  if (process.env.VERIFY_DEPLOYED_BUILD === '1') {
    assert(process.env.BASE_URL, 'VERIFY_DEPLOYED_BUILD requires BASE_URL');
    const html = await fs.readFile(path.join(publicDir, 'index.html'), 'utf8');
    for (const key of ['built', 'through', 'events']) {
      const match = html.match(new RegExp(`data-${key}="([^"]*)"`));
      assert(match, `Local build is missing data-${key}`);
      expected[key] = match[1];
    }
  }
  if (process.env.EXPECTED_BUILD) expected.built = process.env.EXPECTED_BUILD;
  if (process.env.DATA_CUTOFF) expected.through = process.env.DATA_CUTOFF;
  return expected;
}

function matchesStamp(actual, expected) {
  return Object.entries(expected).every(([key, value]) => actual[key] === value);
}

async function waitForDeployment(url, expected) {
  if (!Object.keys(expected).length) return url;
  diagnostics.step = 'wait for deployed build';
  const probe = await browser.newPage();
  activePage = probe;
  const deadline = Date.now() + 90_000;
  let lastError;
  try {
    do {
      const fresh = new URL(url);
      fresh.searchParams.set('_verify_build', String(Date.now()));
      try {
        const response = await probe.goto(fresh.href, {waitUntil: 'domcontentloaded', timeout: 15_000});
        assert(response && response.ok(), `Homepage returned ${response && response.status()}`);
        diagnostics.remoteStamp = await stamp(probe);
        if (matchesStamp(diagnostics.remoteStamp, expected)) return fresh.href;
        lastError = `Expected ${JSON.stringify(expected)}, got ${JSON.stringify(diagnostics.remoteStamp)}`;
      } catch (error) {
        lastError = error.message;
      }
      console.log('Waiting for the deployed build stamp to match.');
      if (Date.now() < deadline) await delay(Math.min(5_000, deadline - Date.now()));
    } while (Date.now() < deadline);
    throw new Error(`Deployment did not become current within 90 seconds: ${lastError}`);
  } finally {
    // On failure leave this page available for the outer screenshot handler.
    if (matchesStamp(diagnostics.remoteStamp || {}, expected)) await probe.close();
  }
}

function monitor(page, origin) {
  if (monitored.has(page)) return;
  monitored.add(page);
  page.on('pageerror', error => diagnostics.pageErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') diagnostics.consoleErrors.push({
      message: message.text(), location: message.location(),
    });
  });
  page.on('response', response => {
    if (new URL(response.url()).origin === origin && response.status() >= 400) {
      diagnostics.assetFailures.push(`${response.status()} ${response.url()}`);
    }
  });
  page.on('requestfailed', request => {
    if (new URL(request.url()).origin === origin) {
      diagnostics.assetFailures.push(`${request.failure()?.errorText} ${request.url()}`);
    }
  });
}

async function load(page, url, expected) {
  activePage = page;
  page.setDefaultTimeout(10_000);
  monitor(page, new URL(url).origin);
  const response = await page.goto(url, {waitUntil: 'networkidle', timeout: 25_000});
  assert(response && response.ok(), `Homepage returned ${response && response.status()}`);
  const actual = await stamp(page);
  assert(actual.built && /^\d+$/.test(actual.events), 'Homepage must carry a valid build stamp');
  assert(matchesStamp(actual, expected), 'Homepage does not match the expected build stamp');
  return actual;
}

async function checkGraph(page, data, rows, days, label, scripted = true) {
  diagnostics.step = label;
  const svg = page.locator('.chart svg');
  assert(await svg.isVisible(), `${label}: graph must be visible`);
  assert.equal(await svg.getAttribute('role'), 'img', `${label}: graph must expose an image role`);
  const scope = (await page.locator('#chart-scope').textContent()).trim();
  assert(scope.startsWith(`${days.length} filing day`), `${label}: incorrect filing-day count: ${scope}`);
  const ariaLabel = await svg.getAttribute('aria-label');
  assert(ariaLabel, `${label}: graph needs an accessible name`);
  if (scripted) {
    assert(ariaLabel.includes(scope), `${label}: accessible name must describe active filters`);
    assert.equal(await svg.locator(':scope > title').textContent(), ariaLabel,
      `${label}: SVG title must agree with its accessible name`);
  }
  const axes = (await svg.locator('.axis').allTextContents()).filter(t => /^\d\d-\d\d$/.test(t));
  assert.deepEqual(axes, days.map(day => day.slice(5)), `${label}: wrong dates on graph axis`);
  const totals = days.map(day => rows.filter(row => row.d === day).length).filter(Boolean);
  assert.deepEqual((await svg.locator('.bar-total').allTextContents()).map(Number), totals,
    `${label}: bar totals differ from matching event rows`);
  const expectedSegments = new Map();
  for (const row of rows) {
    const key = `${row.d} · ${data.labels[row.s]}`;
    expectedSegments.set(key, (expectedSegments.get(key) || 0) + 1);
  }
  const expectedTitles = [...expectedSegments].map(([key, n]) => `${key}: ${n}`).sort();
  assert.deepEqual((await svg.locator('.seg > title').allTextContents()).sort(), expectedTitles,
    `${label}: plotted signals differ from matching event rows`);
  if (!days.length) assert((await svg.textContent()).includes('No filing days for this period'),
    `${label}: an empty period needs an explanation`);
}

async function pick(page, group, attribute, value) {
  const button = page.locator(`${group} button[data-${attribute}="${value}"]`);
  await button.click();
  assert.equal(await button.getAttribute('aria-pressed'), 'true', 'Selected filter needs aria-pressed');
  assert.equal(await page.locator(`${group} button[aria-pressed="true"]`).count(), 1,
    'Exactly one filter in each group should be selected');
}

async function run() {
  diagnostics.url = process.env.BASE_URL ? homepage(process.env.BASE_URL) : await serve();
  const expected = await expectedStamp();
  diagnostics.expectedStamp = expected;
  const playwright = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
  assert(['chromium', 'firefox', 'webkit'].includes(engine), 'Unsupported browser engine');
  browser = await playwright[engine].launch({headless: true,
    ...(engine === 'chromium' && process.env.BROWSER_CHANNEL
      ? {channel: process.env.BROWSER_CHANNEL} : {})});
  const url = await waitForDeployment(diagnostics.url, expected);
  const page = await browser.newPage({viewport: {width: 1365, height: 1000}});
  diagnostics.step = 'load homepage';
  diagnostics.stamp = await load(page, url, expected);
  const navigationURLs = await page.locator('.site-head nav a').evaluateAll(nodes =>
    [...new Set(nodes.map(node => node.href).filter(href => {
      const link = new URL(href);
      return link.origin === location.origin && link.pathname.endsWith('.html');
    }))]);
  assert(navigationURLs.length >= 5, 'Primary navigation must expose the site pages');
  const empty = Number(diagnostics.stamp.events) === 0;
  if (empty) {
    assert.equal(await page.locator('.activity-panel').count(), 0,
      'An empty dataset should not present an activity graph');
    assert(await page.locator('.empty-state').isVisible(), 'Empty dataset needs an explanation');
  } else {
    assert.equal(await page.locator('.activity-panel').evaluate(node => node.open), true,
      'The activity graph must be open by default');
    const data = await page.locator('#chart-data').evaluate(node => JSON.parse(node.textContent));
    assert(data.days.length && data.rows.length, 'Nonempty graph needs days and event rows');
    assert.deepEqual(data.days, [...new Set(data.days)].sort(), 'Chart dates must be unique and sorted');
    assert(data.rows.every(row => data.days.includes(row.d) && data.labels[row.s] && row.f),
      'Every chart row needs a plotted date, signal label and form family');
    const hideRoutine = await page.locator('#routinechips').count() > 0;
    const visibleRows = data.rows.filter(row => !hideRoutine || !row.r);
    await checkGraph(page, data, visibleRows, data.days, 'default graph');
    const refine = page.locator('#refine');
    if (!await refine.evaluate(node => node.open)) await refine.locator('summary').click();
    // A deadline-only window can legitimately contain only hidden notices.
    // Reveal those notices to exercise the controls, then restore the default.
    const revealRoutine = !visibleRows.length && hideRoutine;
    if (revealRoutine) await pick(page, '#routinechips', 'routine', 'show');
    const filterRows = revealRoutine ? data.rows : visibleRows;
    const options = await page.locator('#period option').evaluateAll(nodes => nodes.map(n => n.value));
    const period = options.filter(value => /^\d{4}-\d{2}$/.test(value)
      && filterRows.some(row => row.d.startsWith(value))).sort().pop();
    assert(period, 'Period control must offer a month represented in the graph');
    await page.locator('#period').selectOption(period);
    const days = data.days.filter(day => day.startsWith(period));
    const periodRows = filterRows.filter(row => row.d.startsWith(period));
    await checkGraph(page, data, periodRows, days, 'period filter');
    const forms = await page.locator('#formchips button[data-form]').evaluateAll(
      nodes => nodes.map(node => node.dataset.form).filter(value => value !== 'all'));
    const form = forms.find(value => periodRows.some(row => row.f === value));
    if (forms.length) assert(form, 'Form controls must cover at least one plotted form');
    let formRows = periodRows;
    if (form) {
      await pick(page, '#formchips', 'form', form);
      formRows = periodRows.filter(row => row.f === form);
      await checkGraph(page, data, formRows, days, 'form filter');
    }
    const signals = await page.locator('#chips button[data-filter]').evaluateAll(
      nodes => nodes.map(node => node.dataset.filter).filter(value => value !== 'all'));
    const signal = signals.find(value => formRows.some(row => row.s === value));
    assert(signal, 'Signal controls must cover at least one plotted signal');
    await pick(page, '#chips', 'filter', signal);
    await checkGraph(page, data, formRows.filter(row => row.s === signal), days, 'signal filter');
    const absentPeriod = options.find(value => value !== 'all'
      && !data.days.some(day => day.startsWith(value)));
    if (absentPeriod) {
      await page.locator('#period').selectOption(absentPeriod);
      await checkGraph(page, data, [], [], 'period outside graph window');
    }
    await pick(page, '#chips', 'filter', 'all');
    if (form) await pick(page, '#formchips', 'form', 'all');
    await page.locator('#period').selectOption('all');
    if (revealRoutine) await pick(page, '#routinechips', 'routine', 'hide');
    await refine.locator('summary').click();
    await checkGraph(page, data, visibleRows, data.days, 'filters restored');
    diagnostics.graph = {days: data.days.length, rows: data.rows.length,
      defaultVisibleRows: visibleRows.length, period, form: form || 'single form; control absent', signal};
  }
  for (const width of [390, 320]) {
    diagnostics.step = `mobile ${width}px`;
    await page.setViewportSize({width, height: 844});
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth + 1);
    if (!empty) assert(await page.locator('.chart svg').isVisible(), 'Mobile graph must remain visible');
  }
  diagnostics.step = 'JavaScript disabled';
  const noJS = await browser.newPage({javaScriptEnabled: false, viewport: {width: 1365, height: 1000}});
  const noJSStamp = await load(noJS, url, diagnostics.stamp);
  assert.deepEqual(noJSStamp, diagnostics.stamp, 'Both modes must read the same site build');
  if (!empty) {
    assert.equal(await noJS.locator('.activity-panel').evaluate(node => node.open), true);
    const data = await noJS.locator('#chart-data').evaluate(node => JSON.parse(node.textContent));
    await checkGraph(noJS, data, data.rows, data.days, 'graph without JavaScript', false);
  } else {
    assert(await noJS.locator('.empty-state').isVisible(), 'Empty state must work without JavaScript');
  }
  await page.setViewportSize({width: 1365, height: 1000});
  diagnostics.navigation = [];
  for (const href of navigationURLs) {
    diagnostics.step = `navigation ${new URL(href).pathname}`;
    const target = new URL(href);
    if (process.env.VERIFY_DEPLOYED_BUILD === '1') {
      target.searchParams.set('_verify_build', String(Date.now()));
    }
    await load(page, target.href, diagnostics.stamp);
    assert(await page.locator('main h1').isVisible(), 'Every navigation page needs a visible heading');
    diagnostics.navigation.push(target.pathname);
  }
  assert.deepEqual(diagnostics.pageErrors, [], 'Browser JavaScript errors');
  assert.deepEqual(diagnostics.consoleErrors, [], 'Browser console errors');
  assert.deepEqual(diagnostics.assetFailures, [], 'Failed local assets');
  diagnostics.step = 'passed';
  console.log(JSON.stringify(diagnostics, null, 2));
}

(async () => {
  try {
    await Promise.race([run(), new Promise((_, reject) => {
      timeout = setTimeout(() => reject(new Error('Browser verification exceeded 180 seconds')), 180_000);
    })]);
  } catch (error) {
    diagnostics.error = error.stack || String(error);
    try {
      await fs.mkdir(artifacts, {recursive: true});
      if (activePage && !activePage.isClosed()) {
        try {
          await activePage.screenshot({path: path.join(artifacts, 'failure.png'), timeout: 5_000});
        } catch (screenshotError) {
          diagnostics.screenshotError = screenshotError.message;
        }
      }
      await fs.writeFile(path.join(artifacts, 'failure.json'), JSON.stringify(diagnostics, null, 2) + '\n');
    } catch (artifactError) {
      diagnostics.artifactError = artifactError.message;
    }
    console.error(JSON.stringify(diagnostics, null, 2));
    process.exitCode = 1;
  } finally {
    clearTimeout(timeout);
    if (browser) await browser.close();
    if (server) await new Promise(resolve => {
      server.close(resolve);
      server.closeAllConnections();
    });
  }
})();
