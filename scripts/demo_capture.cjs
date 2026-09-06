#!/usr/bin/env node
/* Reproducible promotional media. Only fictional data in a fresh temp directory.
 * Requires Chrome, ffmpeg, Python with Aigator dependencies, and puppeteer-core.
 * Example:
 * NODE_PATH=/path/to/node_modules AIGATOR_DEMO_PYTHON=.venv/bin/python node scripts/demo_capture.cjs
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const puppeteer = require('puppeteer-core');

const root = path.resolve(__dirname, '..');
const output = path.resolve(process.env.AIGATOR_DEMO_OUTPUT || path.join(root, 'docs/images'));
const port = Number(process.env.AIGATOR_DEMO_PORT || 8767);
const base = `http://127.0.0.1:${port}`;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'aigator-fictional-demo-'));
    const daemon = spawn(process.env.AIGATOR_DEMO_PYTHON || 'python3', [
        path.join(__dirname, 'demo_seed.py'), path.join(temp, 'demo.db'), '--serve', '--port', String(port),
    ], {cwd: root, stdio: 'ignore'});
    let browser;
    try {
        let ready = false;
        for (let tries = 0; tries < 60; tries++) {
            if (daemon.exitCode !== null) throw new Error('Demo server exited. Check Python dependencies or use a free port.');
            try { ready = (await fetch(base)).ok; } catch {}
            if (ready) break;
            await delay(150);
        }
        if (!ready || daemon.exitCode !== null) throw new Error('Demo server did not start.');
        await delay(200);
        if (daemon.exitCode !== null) throw new Error('Demo port is already occupied; choose AIGATOR_DEMO_PORT.');
        browser = await puppeteer.launch({
            executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
            headless: true, args: ['--disable-dev-shm-usage'],
        });
        await browser.defaultBrowserContext().overridePermissions(base, ['clipboard-read', 'clipboard-sanitized-write']);
        const page = await browser.newPage();
        let dialogError = null;
        page.on('dialog', async dialog => {
            dialogError = new Error(`Unexpected demo dialog: ${dialog.message()}`);
            await dialog.dismiss();
        });
        await page.setViewport({width: 1440, height: 960, deviceScaleFactor: 1});
        await page.setRequestInterception(true);
        page.on('request', request => {
            const url = new URL(request.url());
            if (url.origin === base || ['data:', 'blob:'].includes(url.protocol)) request.continue();
            else request.abort();
        });
        await page.goto(base, {waitUntil: 'networkidle0'});
        await page.waitForSelector('.session-item');
        const count = await page.$$eval('.session-item', items => items.length);
        if (count !== 8) throw new Error(`Expected exactly eight fictional sessions, saw ${count}.`);
        const synthetic = await page.$$eval('.session-item', items => items.every(
            item => item.dataset.sessionId?.includes('_fictional-demo-'),
        ));
        if (!synthetic) throw new Error('Refusing to capture: the page does not contain the synthetic demo archive.');
        const sources = await page.$$eval('.source-tag', tags => tags.map(tag => tag.textContent));
        for (const source of ['Claude Code', 'Codex', 'Copilot CLI', 'VS Code Copilot']) {
            if (!sources.includes(source)) throw new Error(`Missing coding-tool demo: ${source}`);
        }
        if (!await page.$eval('#fuzzyToggle', el => el.checked)) throw new Error('Fuzzy search must be enabled by default.');
        fs.mkdirSync(output, {recursive: true});
        await page.click('.session-item');
        await page.waitForSelector('#copySessionBtn');
        await delay(300);
        await page.screenshot({path: path.join(output, 'aigator-dashboard.png')});

        // Constant-rate frames provide reliable pacing without recording dropped frames.
        let frameIndex = 0;
        async function hold(seconds) {
            const png = await page.screenshot();
            for (let frame = 0; frame < seconds * 10; frame++) {
                fs.writeFileSync(path.join(temp, `frame-${String(frameIndex++).padStart(4, '0')}.png`), png);
            }
        }
        await hold(2);
        await page.click('.session-item:nth-child(2)');
        await delay(250);
        await hold(2);
        await page.click('#searchInput');
        await page.type('#searchInput', 'sql srch', {delay: 75});
        await page.waitForFunction(() => document.querySelector('#searchStatus')?.textContent.includes('matches'));
        await page.waitForSelector('.search-snippet mark');
        await hold(1.5);
        await page.click('.session-item');
        await delay(200);
        await hold(2);
        // Show exact-mode fallback, then restore fuzzy before clearing the query.
        await page.click('#fuzzyToggle');
        await page.waitForFunction(() => document.querySelector('#sessionList')?.textContent.includes('No results matching'));
        await hold(1);
        await page.click('#fuzzyToggle');
        await page.waitForSelector('.search-snippet');
        await page.click('#searchInput', {clickCount: 3});
        await page.keyboard.press('Backspace');
        await page.waitForFunction(() => document.querySelectorAll('.session-item').length === 8);
        await page.click('.tab-btn[data-src="codex"]');
        await page.waitForFunction(() => document.querySelectorAll('.session-item').length === 1);
        await page.click('.session-item');
        await delay(200);
        await hold(1.5);
        await page.click('.tab-btn[data-src="gemini_web"]');
        await delay(250);
        await page.click('.session-item');
        await delay(200);
        await hold(2);
        await page.click('#copySessionBtn');
        await page.waitForFunction(() => document.querySelector('#copySessionBtn')?.textContent.includes('Copied'));
        if (dialogError) throw dialogError;
        await hold(1.5);
        await delay(1800);
        await page.click('.tab-btn[data-src=""]');
        await delay(250);
        await page.click('.session-item');
        await delay(200);
        await hold(2);
        await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
        if (overflow) throw new Error('Mobile viewport has horizontal overflow.');
        execFileSync('ffmpeg', [
            '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2', '-filter_complex_threads', '2', '-framerate', '10',
            '-i', path.join(temp, 'frame-%04d.png'),
            '-filter_complex', '[0:v]scale=1080:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3',
            '-loop', '0', path.join(output, 'aigator-demo.gif'),
        ], {stdio: 'inherit'});
        console.log(`Created ${path.join(output, 'aigator-dashboard.png')}`);
        console.log(`Created ${path.join(output, 'aigator-demo.gif')}`);
    } finally {
        if (browser) await browser.close();
        daemon.kill('SIGTERM');
        // temp was created by this script and contains only its synthetic DB/frames.
        fs.rmSync(temp, {recursive: true, force: true});
    }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
