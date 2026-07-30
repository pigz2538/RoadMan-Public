import { expect, test } from '@playwright/test'

test('阶段 D 真实 Agent 行程可展示地图、阶段和 Markdown', async ({ page }) => {
  const tripId = process.env.ROADMAN_E2E_TRIP_ID
  test.skip(!tripId, '需要 ROADMAN_E2E_TRIP_ID 指向已完成的规划行程')

  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await page.goto(`http://127.0.0.1:8080/trips/${tripId}/plan`)
  await expect(page.locator('.plan-top h1')).toContainText('武汉')
  await expect(page.locator('.plan-top h1')).toContainText('行程')
  await expect(page.locator('.progress-banner, .demo-sse')).toHaveCount(0)
  expect(await page.locator('.stage-card').count()).toBeGreaterThanOrEqual(5)
  await expect(page.locator('.stage-card.active')).toHaveCount(1)
  await expect(page.getByText('高德 JSAPI · 真实道路轨迹')).toBeVisible({ timeout: 30_000 })
  const riskSummary = page.getByText('风险与自驾校验', { exact: false })
  await expect(riskSummary).toBeVisible()
  await riskSummary.click()
  expect(await page.locator('.risk-item').count()).toBeGreaterThan(0)
  await expect(page.getByText('查看 Markdown 行程安排')).toBeVisible()
  await page.screenshot({
    path: 'test-results/stage-d-agent-plan.png',
    fullPage: true,
    animations: 'disabled',
  })
  expect(consoleErrors).toEqual([])
})

test('首页在规划前集中询问矛盾与缺失信息', async ({ page }) => {
  await page.goto('http://127.0.0.1:8080/home')
  const input = page.locator('#trip-prompt')
  await input.fill('今天从上海跨海去海岛，昨天返回，下午3点出发到下午3点抵达')
  await page.getByRole('button', { name: '开始规划' }).click()

  const panel = page.locator('.preflight-panel')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  await expect(panel).toContainText('返回日期早于出发日期')
  await expect(panel).toContainText('行程涉及跨海')
  await expect(panel).toContainText('时间窗口只有 0 分钟')
  await expect(page).toHaveURL(/\/home$/)
  await expect(page.getByRole('button', { name: '重新检查' })).toBeVisible()
})
