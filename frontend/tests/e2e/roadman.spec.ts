import { expect, test } from '@playwright/test'

test('首页包含核心规划入口', async ({ page }) => {
  await page.route('**/models/car-concept-optimized.glb', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.continue()
  })
  await page.goto('/home')
  await expect(page.getByRole('heading', { name: /自驾游规划/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /开始规划/ })).toBeVisible()
  const vehicleModel = page.locator('model-viewer')
  await expect(vehicleModel).toBeVisible()
  await expect(vehicleModel).not.toHaveAttribute('poster')
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
    viewer.cameraOrbit = '35deg 70deg 275%'
    viewer.jumpCameraToGoal?.()
  })
  await page.waitForTimeout(300)
  await expect(page).toHaveScreenshot('home.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.03,
  })
})

test('首页 3D 车辆保留滚轮缩放和大画布缓冲', async ({ page }) => {
  test.setTimeout(45_000)
  await page.goto('/home')
  await expect(page.getByText('3D 模型已加载')).toBeVisible({ timeout: 20_000 })
  const vehicleModel = page.locator('model-viewer')
  await expect(vehicleModel).not.toHaveAttribute('disable-zoom')
  const box = await vehicleModel.boundingBox()
  if (!box) throw new Error('3D 车辆画布未完成布局')
  expect(box.width).toBeGreaterThan(page.viewportSize()!.width)
  expect(box.height).toBeGreaterThan(page.viewportSize()!.height)
  const beforeRadius = await vehicleModel.evaluate((model) =>
    (model as HTMLElement & { getCameraOrbit: () => { radius: number } }).getCameraOrbit().radius,
  )
  await page.mouse.move(page.viewportSize()!.width / 2, Math.min(420, box.y + box.height / 2))
  await page.mouse.wheel(0, -700)
  await page.waitForTimeout(500)
  const afterRadius = await vehicleModel.evaluate((model) =>
    (model as HTMLElement & { getCameraOrbit: () => { radius: number } }).getCameraOrbit().radius,
  )
  expect(afterRadius).toBeLessThan(beforeRadius)
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
  const stageCards = page.locator('.stage-card')
  expect(await stageCards.count()).toBe(6)
  const activeStageCard = page.locator('.stage-card.active')
  await expect(activeStageCard).toContainText('黄石服务区')
  await expect(activeStageCard).toContainText('牯岭镇')
  await expect(activeStageCard).toContainText('预计出发')
  await expect(activeStageCard).toContainText('预计抵达')
  await expect(activeStageCard).toContainText('路况')
  await expect(activeStageCard).toContainText('天气')
  await page.getByRole('button', { name: '下一个阶段' }).click()
  await expect(page.getByText(/当前已选：stage_4/)).toBeVisible()
  await expect(page.locator('.trip-sidebar select')).toHaveValue('0')
  await expect(page.locator('.stage-card.active')).toContainText('步行前往景点')
  await page.getByRole('button', { name: '上一个阶段' }).click()
  await expect(page.getByText(/当前已选：stage_2/)).toBeVisible()
  await expect(page.locator('.trip-sidebar select')).toHaveValue('0')

  const track = page.locator('.stage-track')
  const trackBox = await track.boundingBox()
  if (!trackBox) throw new Error('阶段卡片栏未完成布局')
  const scrollBefore = await track.evaluate((element) => element.scrollLeft)
  await page.mouse.move(trackBox.x + trackBox.width * 0.75, trackBox.y + trackBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(trackBox.x + trackBox.width * 0.25, trackBox.y + trackBox.height / 2, { steps: 10 })
  await page.mouse.up()
  const scrollAfter = await track.evaluate((element) => element.scrollLeft)
  expect(scrollAfter).toBeGreaterThan(scrollBefore)
  await page.waitForTimeout(1_200)
  await expect(page).toHaveScreenshot('plan.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.03,
  })

  const map = page.locator('.amap-container')
  const mapBox = await map.boundingBox()
  const markers = page.locator('.amap-terminal-marker, .amap-poi-marker')
  expect(await markers.count()).toBeGreaterThan(0)
  const marker = markers.filter({ hasText: '黄石服务区' }).first()
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
