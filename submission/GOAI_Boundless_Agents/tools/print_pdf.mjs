import { chromium } from '../../../frontend/node_modules/playwright/index.mjs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const htmlPath = path.join(root, 'RoadMan_赛道二参赛方案书.html');
const pdfPath = path.join(root, 'RoadMan_赛道二参赛方案书.pdf');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'networkidle' });
await page.emulateMedia({ media: 'print' });
await page.pdf({
  path: pdfPath,
  format: 'A4',
  printBackground: true,
  preferCSSPageSize: true,
  displayHeaderFooter: true,
  headerTemplate: '<div></div>',
  footerTemplate: '<div style="width:100%;font-family:Microsoft YaHei UI,Arial,sans-serif;font-size:8px;color:#7f91aa;padding:0 16mm;display:flex;justify-content:space-between"><span>RoadMan · GOAI 无界应用参赛方案</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
  margin: { top:'17mm', right:'16mm', bottom:'18mm', left:'16mm' }
});
await browser.close();
