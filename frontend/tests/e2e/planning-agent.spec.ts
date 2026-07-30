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
  await expect(page.locator('.stage-card')).toHaveCount(8)
  await expect(page.locator('.stage-card.active')).toHaveCount(1)
  await expect(page.getByText('高德 JSAPI · 真实道路轨迹')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('查看 Markdown 路书')).toBeVisible()
  await page.screenshot({
    path: 'test-results/stage-d-agent-plan.png',
    fullPage: true,
    animations: 'disabled',
  })
  expect(consoleErrors).toEqual([])
})
