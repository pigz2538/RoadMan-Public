import { readFile, readdir, mkdir, writeFile, unlink } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../frontend/node_modules/playwright/index.mjs'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const uiBase = process.env.ROADMAN_UI_BASE || 'http://127.0.0.1:8080'
const apiBase = process.env.ROADMAN_API_BASE || 'http://127.0.0.1:8000'
const limit = Number(process.env.ROADMAN_SHOWCASE_LIMIT || 10)
const startId = Number(process.env.ROADMAN_SHOWCASE_START || 1)
const timeoutMs = Number(process.env.ROADMAN_SHOWCASE_TIMEOUT_MS || 900000)
const outputDir = join(root, 'artifacts', 'showcase', 'prompt-screens')

const caseAnswers = {
  1: { origin_name: '武汉', destination_name: '北京', start_date: '2026-08-21', end_date: '2026-08-23' },
  2: { origin_name: '上海', destination_name: '黄山', start_date: '2026-08-24', end_date: '2026-08-26' },
  3: { origin_name: '武汉', destination_name: '九宫山', start_date: '2027-08-12', end_date: '2027-08-14' },
  4: { origin_name: '武汉', destination_name: '南京', start_date: '2026-09-04', end_date: '2026-09-08' },
  5: { origin_name: '北海', destination_name: '涠洲岛', start_date: '2026-10-01', end_date: '2026-10-04' },
  6: { origin_name: '武汉', destination_name: '北京', start_date: '2026-08-21', end_date: '2026-08-23' },
  7: { origin_name: '武汉', destination_name: '神农架', start_date: '2026-09-18', end_date: '2026-09-20' },
  8: { origin_name: '武汉', destination_name: '杭州', start_date: '2026-09-19', end_date: '2026-09-20' },
  9: { origin_name: '武汉', destination_name: '成都', start_date: '2026-09-21', end_date: '2026-09-23' },
  10: { origin_name: '武汉', destination_name: '成都', start_date: '2026-09-25', end_date: '2026-09-28' },
}

async function promptFile() {
  const dir = join(root, 'submission', 'GOAI_Boundless_Agents')
  const names = await readdir(dir)
  const name = names.find((item) => item.startsWith('RoadMan_10_') && item.endsWith('.md'))
  if (!name) throw new Error('showcase prompt markdown not found')
  return join(dir, name)
}

function parsePrompts(markdown) {
  const results = []
  const heading = /##\s+(\d{2})｜[^\n]+[\s\S]*?\*\*直接输入：\*\*\s*\n\n```text\n([\s\S]*?)\n```/g
  let match
  while ((match = heading.exec(markdown))) {
    results.push({ id: Number(match[1]), text: match[2].trim() })
  }
  return results.sort((a, b) => a.id - b.id)
}

function answerFor(caseId, issue, prompt) {
  const field = issue.field || ''
  const known = caseAnswers[caseId] || {}
  if (field === 'origin_name' && known.origin_name) return known.origin_name
  if (field === 'destination_name' && known.destination_name) return known.destination_name
  if (field === 'start_date' && known.start_date) return known.start_date
  if (field === 'end_date' && known.end_date) return known.end_date
  if (field === 'time_window') return '按合理车程和实际班次安排，不强行压缩时间'
  if (field === 'preferences') return issue.options?.[0] || '按安全、舒适和可执行性安排'
  return issue.options?.[0] || '按智能助手建议处理'
}

async function textOrEmpty(locator) {
  try { return await locator.innerText({ timeout: 5000 }) } catch { return '' }
}

async function fetchTrip(id) {
  const response = await fetch(`${apiBase}/api/v1/trips/${id}`)
  if (!response.ok) throw new Error(`trip fetch ${response.status}`)
  return response.json()
}

async function fetchPlanning(id) {
  const response = await fetch(`${apiBase}/api/v1/trips/${id}/planning`)
  if (!response.ok) throw new Error(`planning fetch ${response.status}`)
  return response.json()
}

async function waitForTrip(id, page) {
  const deadline = Date.now() + timeoutMs
  let last = ''
  while (Date.now() < deadline) {
    const [trip, planning] = await Promise.all([fetchTrip(id), fetchPlanning(id)])
    const progress = planning.progress || {}
    const state = `${trip.status}:${progress.value || 0}:${progress.node || ''}`
    if (state !== last) {
      console.log(`[showcase ${id}] ${state}`)
      last = state
    }
    if (trip.status === 'completed' || trip.status === 'failed' || trip.status === 'clarification_required') {
      return { trip, planning }
    }
    await page.waitForTimeout(2500)
  }
  throw new Error(`planning timeout after ${timeoutMs}ms`)
}

async function clickUnique(locator, label) {
  const count = await locator.count()
  if (count !== 1) throw new Error(`${label}: expected one element, got ${count}`)
  await locator.click({ timeout: 300000 })
}

async function runCase(browser, item) {
  const id = String(item.id).padStart(2, '0')
  const screenshot = join(outputDir, `${id}-${item.id === 1 ? 'main-cross-city' : item.id === 2 ? 'family-ev' : item.id === 3 ? 'perseids' : item.id === 4 ? 'nanjing-research' : item.id === 5 ? 'ferry-island' : item.id === 6 ? 'beijing-weekend' : item.id === 7 ? 'shennongjia-family' : item.id === 8 ? 'hangzhou-transit' : item.id === 9 ? 'chengdu-business' : 'must-visit-worksites'}.png`)
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' })
  const page = await context.newPage()
  const result = { id: item.id, prompt: item.text, screenshot: null, status: 'failed', preflight: [], planning: null, errors: [] }
  try {
    await page.goto(`${uiBase}/home`, { waitUntil: 'domcontentloaded', timeout: 120000 })
    const promptInput = page.locator('#trip-prompt')
    await promptInput.waitFor({ state: 'visible', timeout: 120000 })
    await promptInput.fill(item.text)
    const startButton = page.getByRole('button', { name: '开始规划', exact: true })
    await clickUnique(startButton, 'start planning button')

    const panel = page.locator('.preflight-panel')
    await panel.waitFor({ state: 'visible', timeout: 300000 })
    for (let rounds = 0; rounds < 12; rounds += 1) {
      const panelText = await textOrEmpty(panel)
      result.preflight.push(panelText)
      if (panelText.includes('?')) {
        throw new Error(`preflight contains replacement question marks: ${JSON.stringify(panelText)}`)
      }
      if (panelText.includes('确认无误，开始规划')) {
        const confirm = panel.getByRole('button', { name: '确认无误，开始规划', exact: true })
        await clickUnique(confirm, 'final confirmation button')
        break
      }
      const answerInput = panel.locator('input.preflight-answer')
      const inputCount = await answerInput.count()
      if (inputCount === 1) {
        const issueText = panelText
        const issue = { field: issueText.includes('从哪里出发') ? 'origin_name' : issueText.includes('主要目的地') ? 'destination_name' : issueText.includes('出发日期') ? 'start_date' : issueText.includes('返回日期') ? 'end_date' : issueText.includes('时间窗口') ? 'time_window' : 'preferences' }
        await answerInput.fill(answerFor(item.id, issue, item.text))
      } else {
        const options = panel.locator('.preflight-options button')
        const count = await options.count()
        if (count < 1) throw new Error(`cannot find clarification control: ${JSON.stringify(panelText)}`)
        await options.nth(0).click({ timeout: 300000 })
      }
      const submit = panel.getByRole('button', { name: /下一个问题|重新检查全部条件/, exact: false })
      const submitCount = await submit.count()
      if (submitCount !== 1) throw new Error(`clarification submit button count: ${submitCount}`)
      // The final clarification starts a second semantic preflight request.
      // Vue keeps the old question visible and disables the button while that
      // request is in flight; do not submit the stale question a second time.
      if (!(await submit.isEnabled())) {
        await page.waitForTimeout(1000)
        continue
      }
      await submit.click({ timeout: 300000 })
      await page.waitForTimeout(700)
    }
    await page.waitForURL(/\/trips\/[^/]+\/plan/, { timeout: 300000 })
    const tripIdMatch = page.url().match(/\/trips\/([^/]+)\/plan/)
    if (!tripIdMatch) throw new Error(`could not read trip id from ${page.url()}`)
    const tripId = tripIdMatch[1]
    const completed = await waitForTrip(tripId, page)
    result.planning = {
      trip_id: tripId,
      status: completed.trip.status,
      days: (completed.trip.days || []).length,
      verification: completed.planning.verification_result || null,
      progress: completed.planning.progress || null,
    }
    if (completed.trip.status !== 'completed' || !completed.planning.verification_result?.passed) {
      throw new Error(`planning did not complete: ${JSON.stringify(result.planning)}`)
    }
    await page.waitForTimeout(2500)
    await page.screenshot({ path: screenshot, fullPage: false })
    result.screenshot = screenshot.replace(`${root}\\`, '').replaceAll('\\', '/')
    result.status = 'passed'
  } catch (error) {
    result.errors.push(String(error?.stack || error))
    try {
      const visible = await textOrEmpty(page.locator('body'))
      result.visible_tail = visible.slice(-2500)
    } catch {}
  } finally {
    await context.close()
  }
  return result
}

await mkdir(outputDir, { recursive: true })
const markdown = await readFile(await promptFile(), 'utf8')
const prompts = parsePrompts(markdown).filter((item) => item.id >= startId).slice(0, limit)
if (!prompts.length) throw new Error('no showcase prompts parsed')
const browser = await chromium.launch({ headless: true })
let results = []
if (startId > 1) {
  try {
    const previous = JSON.parse(await readFile(join(root, 'artifacts', 'showcase', 'prompt-results.json'), 'utf8'))
    results = Array.isArray(previous.results) ? previous.results.filter((item) => Number(item.id) < startId) : []
  } catch {}
}
try {
  for (const item of prompts) {
    const target = join(outputDir, `${String(item.id).padStart(2, '0')}-`)
    for (const name of await readdir(outputDir)) {
      if (name.startsWith(target.split('\\').pop())) await unlink(join(outputDir, name)).catch(() => {})
    }
    const result = await runCase(browser, item)
    results.push(result)
    await writeFile(join(root, 'artifacts', 'showcase', 'prompt-results.json'), JSON.stringify({ generated_at: new Date().toISOString(), ui_base: uiBase, api_base: apiBase, results }, null, 2), 'utf8')
    console.log(`[showcase ${String(item.id).padStart(2, '0')}] ${result.status}`)
  }
} finally {
  await browser.close()
}
console.log(JSON.stringify({ passed: results.filter((item) => item.status === 'passed').length, failed: results.filter((item) => item.status !== 'passed').length, results }, null, 2))
