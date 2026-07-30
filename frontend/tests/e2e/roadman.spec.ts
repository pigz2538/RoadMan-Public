import { expect, test } from '@playwright/test'

test('首页包含核心规划入口', async ({ page }) => {
  await page.goto('/home')
  await expect(page.getByRole('heading', { name: /自驾游规划/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /开始规划/ })).toBeVisible()
  await expect(page.locator('model-viewer')).toBeVisible()
  await expect(page.getByText('3D 模型已加载')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: '打开账户菜单' }).click()
  await expect(page.getByRole('navigation', { name: '账户菜单' })).toBeVisible()
  await page.getByRole('button', { name: '打开账户菜单' }).click()
  await page.locator('model-viewer').evaluate((model) => {
    model.removeAttribute('auto-rotate')
    const viewer = model as HTMLElement & {
      cameraOrbit: string
      jumpCameraToGoal?: () => void
      resetTurntableRotation?: (theta?: number) => void
    }
    viewer.resetTurntableRotation?.(0)
    viewer.cameraOrbit = '35deg 70deg 75%'
    viewer.jumpCameraToGoal?.()
  })
  await page.waitForTimeout(300)
  await expect(page).toHaveScreenshot('home.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.03,
  })
})

test('规划页支持天、阶段和节点选择', async ({ page }) => {
  page.on('console', (message) => {
    if (message.type() === 'warning') console.warn(message.text())
  })
  await page.goto('/trips/trip_wuhan_lushan_demo/plan')
  await expect(page.getByText('武汉—庐山两天一夜自然之旅')).toBeVisible()
  await expect(page.getByText('高德 JSAPI · 真实道路轨迹')).toBeVisible({ timeout: 25_000 })
  await page.getByRole('button', { name: /高速转盘山公路/ }).click()
  await expect(page.getByText(/当前已选：stage_2/)).toBeVisible()
  await expect(page).toHaveScreenshot('plan.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.03,
  })

  const map = page.locator('.amap-container')
  const mapBox = await map.boundingBox()
  const markers = page.locator('.amap-number-marker')
  expect(await markers.count()).toBeGreaterThan(0)
  const marker = markers.first()
  const before = await marker.boundingBox()
  if (!mapBox || !before) throw new Error('高德地图或 Marker 未完成布局')
  await page.mouse.move(mapBox.x + mapBox.width / 2, mapBox.y + mapBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(mapBox.x + mapBox.width / 2 + 90, mapBox.y + mapBox.height / 2 + 35, { steps: 8 })
  await page.mouse.up()
  await page.mouse.wheel(0, -300)
  await page.waitForTimeout(250)
  const after = await marker.boundingBox()
  if (!after) throw new Error('地图平移缩放后 Marker 丢失')
  expect(Math.abs(after.x - before.x)).toBeGreaterThan(10)
  await expect(page.getByText('高德 JSAPI · 真实道路轨迹')).toBeVisible()
})
