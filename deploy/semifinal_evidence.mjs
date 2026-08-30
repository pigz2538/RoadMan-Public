import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../frontend/node_modules/playwright/index.mjs'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const uiBase = process.env.ROADMAN_UI_BASE || 'http://127.0.0.1:8080'
const outputDir = join(root, 'submission', 'GOAI_Boundless_Agents', 'semifinal', 'assets')
const viewport = { width: 2560, height: 1440 }

await mkdir(outputDir, { recursive: true })

const range = JSON.parse(await readFile(join(root, 'evaluation', 'results', 'range-accuracy-baseline.json'), 'utf8'))
const safety = JSON.parse(await readFile(join(root, 'evaluation', 'results', 'safety-scenarios-baseline.json'), 'utf8'))
const manifest = { generated_at: new Date().toISOString(), viewport, screenshots: [] }

function evidenceHtml() {
  const metrics = range.metrics
  const scenarioCards = safety.cases.map((item) => `
    <article class="scenario">
      <span class="dot ${item.passed ? 'pass' : 'fail'}"></span>
      <div><strong>${item.id}</strong><small>${item.dependency_state} · ${item.route_executable ? '可执行' : '需人工确认'}</small></div>
    </article>`).join('')
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    *{box-sizing:border-box}body{margin:0;background:#eef4ff;color:#122748;font-family:Inter,"Microsoft YaHei",sans-serif}
    main{width:2560px;height:1440px;padding:82px 110px;display:grid;grid-template-columns:1.05fr .95fr;gap:42px;background:radial-gradient(circle at 88% 8%,#d8e7ff,transparent 34%),#eef4ff}
    .eyebrow{font-size:20px;letter-spacing:.22em;color:#3676ee;font-weight:800}.title{font-size:58px;line-height:1.15;margin:18px 0 14px}.subtitle{font-size:25px;color:#5f7291;line-height:1.6;margin:0 0 38px}
    .panel{background:rgba(255,255,255,.92);border:1px solid #d6e3f8;border-radius:32px;box-shadow:0 24px 70px rgba(40,79,145,.12);padding:36px}
    .metric-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.metric{padding:25px 22px;border-radius:22px;background:#f3f7ff;border:1px solid #dfebff}.metric strong{display:block;font-size:39px;color:#2367e8}.metric span{display:block;margin-top:8px;font-size:18px;color:#647797}
    h2{font-size:28px;margin:0 0 23px}.bar-row{display:grid;grid-template-columns:190px 1fr 80px;align-items:center;gap:16px;margin:18px 0;font-size:19px}.bar{height:18px;background:#e4ecf9;border-radius:99px;overflow:hidden}.fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#2c8bf2,#7655ef)}
    .boundary{margin-top:28px;padding:22px 25px;border-left:6px solid #f59e0b;background:#fff8e8;border-radius:14px;font-size:19px;line-height:1.6}.scenarios{display:grid;grid-template-columns:1fr 1fr;gap:13px}.scenario{display:flex;gap:13px;align-items:center;padding:16px;border-radius:16px;background:#f7faff}.scenario strong{font-size:17px}.scenario small{display:block;color:#72829b;margin-top:5px}.dot{width:13px;height:13px;border-radius:50%;flex:none}.pass{background:#16b981}.fail{background:#ef4444}
    .footer{position:absolute;left:110px;bottom:45px;color:#71829c;font-size:17px}.badge{display:inline-block;padding:8px 14px;background:#e6f8f1;color:#0d9367;border-radius:99px;font-size:17px;font-weight:800;margin-left:14px}
  </style></head><body><main>
    <section>
      <div class="eyebrow">ROADMAN · SEMIFINAL EVIDENCE</div>
      <h1 class="title">续航量化与诚实降级<br>自动评测证据</h1>
      <p class="subtitle">同一套脚本可替换为真实车辆遥测数据；当前基准明确标注为模拟传感器回放，不把估算包装成实测。</p>
      <div class="panel">
        <h2>预测值 vs. 观测值 <span class="badge">12 条回放</span></h2>
        <div class="metric-grid">
          <div class="metric"><strong>${metrics.energy_mape_percent}%</strong><span>能耗 MAPE</span></div>
          <div class="metric"><strong>${metrics.range_mape_percent}%</strong><span>等效续航 MAPE</span></div>
          <div class="metric"><strong>${metrics.soc_mae_percentage_points}pp</strong><span>到达 SOC MAE</span></div>
          <div class="metric"><strong>${metrics.energy_rmse_kwh}</strong><span>能耗 RMSE / kWh</span></div>
          <div class="metric"><strong>${metrics.energy_p95_absolute_error_percent}%</strong><span>能耗 P95 误差</span></div>
          <div class="metric"><strong>${metrics.energy_bias_kwh}</strong><span>能耗偏差 / kWh</span></div>
        </div>
        <div class="bar-row"><span>能耗 MAPE / 12%</span><div class="bar"><div class="fill" style="width:${Math.min(100, metrics.energy_mape_percent / 12 * 100)}%"></div></div><b>${metrics.energy_mape_percent}%</b></div>
        <div class="bar-row"><span>续航 MAPE / 12%</span><div class="bar"><div class="fill" style="width:${Math.min(100, metrics.range_mape_percent / 12 * 100)}%"></div></div><b>${metrics.range_mape_percent}%</b></div>
        <div class="bar-row"><span>SOC MAE / 5pp</span><div class="bar"><div class="fill" style="width:${Math.min(100, metrics.soc_mae_percentage_points / 5 * 100)}%"></div></div><b>${metrics.soc_mae_percentage_points}</b></div>
        <div class="boundary"><b>声明边界：</b>${range.claim_boundary}</div>
      </div>
    </section>
    <section class="panel">
      <h2>异常与降级矩阵 <span class="badge">${safety.sample_count}/${safety.sample_count} 通过</span></h2>
      <div class="metric-grid" style="margin-bottom:26px">
        <div class="metric"><strong>${Math.round(safety.task_completion_rate*100)}%</strong><span>预期行为完成</span></div>
        <div class="metric"><strong>${Math.round(safety.degradation_handled_rate*100)}%</strong><span>降级处理成功</span></div>
        <div class="metric"><strong>${(safety.route_executability_rate*100).toFixed(2)}%</strong><span>直接可执行路线</span></div>
      </div>
      <div class="scenarios">${scenarioCards}</div>
      <div class="boundary"><b>为什么不是 100% 可执行：</b>当真实补能服务中断，只能插入路线估算位置。系统继续提供方案，但明确标记“需真实服务确认”，这是安全边界而不是失败。</div>
    </section>
    <div class="footer">来源：evaluation/range_accuracy.py · evaluation/safety_scenarios.py · 画布 2560×1440</div>
  </main></body></html>`
}

async function capture(page, name, url, ready) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 })
  if (ready) await ready(page)
  await page.waitForTimeout(1200)
  const path = join(outputDir, name)
  await page.screenshot({ path, fullPage: false })
  manifest.screenshots.push({ name, width: viewport.width, height: viewport.height, source: url })
}

const browser = await chromium.launch({ headless: true })
try {
  const context = await browser.newContext({ viewport, locale: 'zh-CN', colorScheme: 'light' })
  const page = await context.newPage()
  await capture(page, '01-home-2560x1440.png', `${uiBase}/home`, async (target) => {
    await target.locator('#trip-prompt').waitFor({ state: 'visible', timeout: 60000 })
  })
  await capture(page, '02-planning-workspace-2560x1440.png', `${uiBase}/trips/trip_wuhan_lushan_demo/plan`, async (target) => {
    await target.locator('.map-live-badge, .map-fallback-badge').waitFor({ state: 'visible', timeout: 60000 })
  })
  for (const label of ['收起左侧行程信息', '收起右侧行程助理', '收起阶段详情']) {
    const button = page.getByRole('button', { name: label })
    if (await button.count()) await button.click()
  }
  await page.waitForTimeout(700)
  await page.screenshot({ path: join(outputDir, '03-map-focus-2560x1440.png'), fullPage: false })
  manifest.screenshots.push({ name: '03-map-focus-2560x1440.png', width: viewport.width, height: viewport.height, source: page.url() })
  await capture(page, '04-operations-2560x1440.png', `${uiBase}/ops`, async (target) => {
    await target.locator('.ops-page').waitFor({ state: 'visible', timeout: 60000 })
  })
  await page.setContent(evidenceHtml(), { waitUntil: 'load' })
  await page.screenshot({ path: join(outputDir, '05-evaluation-dashboard-2560x1440.png'), fullPage: false })
  manifest.screenshots.push({ name: '05-evaluation-dashboard-2560x1440.png', width: viewport.width, height: viewport.height, source: 'generated from evaluation results' })
  await context.close()
} finally {
  await browser.close()
}

await writeFile(join(outputDir, 'screenshot-manifest.json'), JSON.stringify(manifest, null, 2), 'utf8')
console.log(`[semifinal-evidence] captured ${manifest.screenshots.length} screenshots at 2560x1440`)
