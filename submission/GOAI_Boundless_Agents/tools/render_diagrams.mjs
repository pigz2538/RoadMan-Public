import { chromium } from '../../../frontend/node_modules/playwright/index.mjs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';

const here = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = path.join(here, 'diagrams.html');
const outDir = path.resolve(here, '..', 'assets', 'diagrams');
await fs.mkdir(outDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1.5 });
await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'networkidle' });
for (const id of ['architecture','agents','journey','state','traceability','safety','deployment']) {
  await page.locator(`#${id}`).screenshot({ path: path.join(outDir, `${id}.png`) });
}
await browser.close();
