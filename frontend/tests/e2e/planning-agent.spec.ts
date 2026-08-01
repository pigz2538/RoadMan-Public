import { expect, test } from '@playwright/test'

test('正常需求预检后进入最终确认而不是空阻塞', async ({ page }) => {
  test.setTimeout(60_000)
  await page.goto('http://127.0.0.1:8080/home')
  await page.locator('#trip-prompt').fill('周六早上从武汉出发，去庐山两天一夜，周日晚八点前回来，喜欢自然景观')
  await page.getByRole('button', { name: '开始规划' }).click()
  const panel = page.locator('.preflight-panel')
  await expect(panel).toContainText('最终确认', { timeout: 45_000 })
  await expect(panel).toContainText('武汉')
  await expect(panel).toContainText('庐山')
  await expect(panel.getByRole('button', { name: '确认无误，开始规划' })).toBeVisible()
})

test('预检异常空响应提供可恢复操作', async ({ page }) => {
  await page.route('**/api/v1/trips/preflight', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      ready: false,
      confirmation_required: false,
      semantic_checked: false,
      issues: [],
      extracted: {},
      summary: {},
    }),
  }))
  await page.goto('http://127.0.0.1:8080/home')
  await page.locator('#trip-prompt').fill('从武汉出发去庐山两天一夜')
  await page.getByRole('button', { name: '开始规划' }).click()
  const panel = page.locator('.preflight-panel')
  await expect(panel).toContainText('没有收到需要补充的具体问题')
  await expect(panel.getByRole('button', { name: '重新检查' })).toBeVisible()
  await expect(panel.getByRole('button', { name: '返回修改' })).toBeVisible()
})

test('阶段 D 真实 Agent 行程可展示地图、阶段和 Markdown', async ({ page }) => {
  const tripId = process.env.ROADMAN_E2E_TRIP_ID
  test.skip(!tripId, '需要 ROADMAN_E2E_TRIP_ID 指向已完成的规划行程')

  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await page.goto(`http://127.0.0.1:8080/trips/${tripId}/plan`)
  await expect(page.locator('.plan-top h1')).toContainText('行程')
  await expect(page.locator('.progress-banner, .demo-sse')).toHaveCount(0)
  expect(await page.locator('.stage-card').count()).toBeGreaterThanOrEqual(5)
  await expect(page.locator('.stage-card.active')).toHaveCount(1)
  await expect(page.locator('.map-live-badge')).toBeVisible({ timeout: 30_000 })
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
  test.setTimeout(60_000)
  await page.goto('http://127.0.0.1:8080/home')
  const input = page.locator('#trip-prompt')
  await input.fill(
    '2026-08-02从上海出发跨海去普陀山，2026-08-01返回，下午3点出发到下午3点抵达',
  )
  await page.getByRole('button', { name: '开始规划' }).click()

  const panel = page.locator('.preflight-panel')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  await expect(panel).toContainText('返回日期早于出发日期')
  await expect(page).toHaveURL(/\/home$/)
  await panel.locator('.preflight-answer').fill('2026-08-03')
  await panel.getByRole('button', { name: '下一个问题' }).click()
  await expect(panel).toContainText('行程涉及跨海')
  await panel.getByRole('button', { name: '轮渡' }).click()
  await panel.getByRole('button', { name: '下一个问题' }).click()
  await expect(panel).toContainText('时间窗口只有 0 分钟')
  await panel.locator('.preflight-answer').fill('取消原到达限制，按合理车程安排')
  await panel.getByRole('button', { name: '重新检查全部条件' }).click()
  await expect(panel).toContainText('最终确认')
  await expect(panel).toContainText('上海')
  await expect(panel).toContainText('普陀山')
  await expect(panel).toContainText('2026-08-03')
  await expect(panel.getByRole('button', { name: '确认无误，开始规划' })).toBeVisible()
})
