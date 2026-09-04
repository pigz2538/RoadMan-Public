import { chromium } from '../../../frontend/node_modules/playwright/index.mjs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const subdir = process.argv[3] || '';
const root = path.join(path.resolve(here, '..'), subdir);
const name = process.argv[2] || 'RoadMan_复赛项目介绍';
const htmlPath = path.join(root, name + '.html');
const pdfPath = path.join(root, name + '.pdf');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'networkidle' });
await page.emulateMedia({ media: 'print' });
await page.pdf({
  path: pdfPath,
  format: 'A4',
  printBackground: true,
  preferCSSPageSize: true,
});
await browser.close();
console.log('PDF written:', pdfPath);
