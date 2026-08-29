import { expect, test } from '@playwright/test'

test('首页包含核心规划入口', async ({ page }) => {
  await page.route('https://www.gstatic.com/**', (route) => route.abort())
  await page.route('**/models/mclaren.glb', async (route) => {
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
    viewer.cameraOrbit = '35deg 70deg 220%'
    viewer.jumpCameraToGoal?.()
  })
  await page.waitForTimeout(300)
  await expect(page).toHaveScreenshot('home.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.03,
    timeout: 10_000,
  })
})

test('首页 3D 车辆保留滚轮缩放和大画布缓冲', async ({ page }) => {
  test.setTimeout(45_000)
  await page.route('https://www.gstatic.com/**', (route) => route.abort())
  await page.goto('/home')
  await expect(page.getByText('3D 模型已加载')).toBeVisible({ timeout: 20_000 })
  const vehicleModel = page.locator('model-viewer')
  await expect(vehicleModel).not.toHaveAttribute('disable-zoom')
  const box = await vehicleModel.boundingBox()
  if (!box) throw new Error('3D 车辆画布未完成布局')
  expect(box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  expect(box.height).toBeLessThanOrEqual(page.viewportSize()!.height)
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

test('首页空输入显示外置灰色提示并保留上次需求', async ({ page }) => {
  await page.goto('/home')
  const input = page.locator('#trip-prompt')
  await expect(input).toHaveValue('')
  await expect(page.locator('.prompt-suggestion')).toContainText('可以这样描述')
  await input.fill('8月11日中午从南浔出发去乌镇，情侣舒适出游')
  await page.reload()
  await expect(input).toHaveValue('8月11日中午从南浔出发去乌镇，情侣舒适出游')
})

test('规划页支持天、阶段和节点选择', async ({ page }) => {
  page.on('console', (message) => {
    if (message.type() === 'warning') console.warn(message.text())
  })
  await page.goto('/trips/trip_wuhan_lushan_demo/plan')
  await expect(page.getByText('武汉—庐山两天一夜自然之旅')).toBeVisible()
  // CI/local environments may not inject a browser AMap key; the safe Mock
  // map is an intentional fallback and should remain testable.
  await expect(page.locator('.map-live-badge, .map-fallback-badge')).toBeVisible({ timeout: 25_000 })
  await page.locator('.stage-card').filter({ hasText: '高速转盘山公路' }).click()
  await expect(page.getByText(/当前阶段：高速转盘山公路/)).toBeVisible()
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
  await expect(page.getByText(/当前阶段：午餐后步行前往景点/)).toBeVisible()
  await expect(page.locator('.trip-sidebar select')).toHaveValue('0')
  await expect(page.locator('.stage-card.active')).toContainText('步行前往景点')
  await page.getByRole('button', { name: '上一个阶段' }).click()
  await expect(page.getByText(/当前阶段：高速转盘山公路/)).toBeVisible()
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
    timeout: 10_000,
  })

  const markers = page.locator('.amap-terminal-marker, .amap-poi-marker, .map-pin')
  expect(await markers.count()).toBeGreaterThan(0)
  const liveMap = page.locator('.amap-container:visible').first()
  if (await liveMap.count()) {
    const mapBox = await liveMap.boundingBox()
    const namedMarker = page.locator('.amap-terminal-marker, .amap-poi-marker').filter({ hasText: '黄石服务区' }).first()
    const marker = (await namedMarker.count()) > 0 ? namedMarker : markers.first()
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
  } else {
    await expect(page.locator('.mock-map:visible')).toBeVisible()
  }
  await expect(page.locator('.map-live-badge, .map-fallback-badge')).toBeVisible()
})

test('规划页三侧信息栏可折叠并保留简化阶段切换', async ({ page }) => {
  await page.goto('/trips/trip_wuhan_lushan_demo/plan')
  await expect(page.locator('.map-live-badge, .map-fallback-badge')).toBeVisible({ timeout: 25_000 })
  await page.locator('.stage-card').first().click()
  const workspace = page.locator('.map-workspace')
  const initialBox = await workspace.boundingBox()
  if (!initialBox) throw new Error('地图工作区未完成布局')

  await page.getByRole('button', { name: '收起左侧行程信息' }).click()
  await expect(page.locator('.trip-sidebar')).toBeHidden()
  const leftCollapsedBox = await workspace.boundingBox()
  expect(leftCollapsedBox!.width).toBeGreaterThan(initialBox.width + 200)

  await page.getByRole('button', { name: '收起右侧行程助理' }).click()
  await expect(page.locator('.agent-panel')).toBeHidden()
  const bothCollapsedBox = await workspace.boundingBox()
  expect(bothCollapsedBox!.width).toBeGreaterThan(leftCollapsedBox!.width + 200)

  await page.getByRole('button', { name: '收起阶段详情' }).click()
  await expect(page.locator('.stage-nav')).toBeHidden()
  const compact = page.getByRole('navigation', { name: '简化阶段切换' })
  await expect(compact).toBeVisible()
  await expect(compact).toContainText('第 1 / 6 段')
  await compact.getByRole('button', { name: '下一个阶段' }).click()
  await expect(compact).toContainText('第 2 / 6 段')

  await page.reload()
  await expect(page.getByRole('button', { name: '展开左侧行程信息' })).toBeVisible()
  await expect(page.getByRole('button', { name: '展开右侧行程助理' })).toBeVisible()
  await expect(page.getByRole('button', { name: '展开阶段详情' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '简化阶段切换' })).toBeVisible()
})

test('规划校验失败以中性弹窗展示原因和偏好建议', async ({ page }) => {
  await page.route('**/api/v1/trips/failure-demo', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'failure-demo',
        title: '需要调整的行程',
        status: 'failed',
        days: [],
        warnings: [],
        request: { raw_text: '自然景观旅行', defaults_applied: [], preferences: ['自然景观', '不走夜路'] },
      }),
    })
  })
  await page.route('**/api/v1/trips/failure-demo/planning', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        trip_id: 'failure-demo',
        status: 'failed',
        missing_fields: [],
        clarification_round: 0,
        defaults_applied: [],
        progress: { node: 'verify_plan', value: 100 },
        verification_result: {
          passed: false,
          issues: [{ code: 'TIME_WINDOW', description: '连续移动时间超出可接受范围。' }],
        },
      }),
    })
  })

  await page.goto('/trips/failure-demo/plan')
  const dialog = page.getByRole('alertdialog', { name: '这次安排需要调整' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('连续移动时间超出可接受范围')
  await expect(dialog).toContainText('自然景观、不走夜路')
  await expect(dialog).toContainText('返回修改需求')
  await expect(dialog).toContainText('重新规划')
  await expect(page.locator('.planning-error')).toHaveCount(0)
})

test('Agent 备选方案先预览再应用', async ({ page }) => {
  await page.route('**/api/v1/trips/trip_wuhan_lushan_demo/recommendations?category=attractions', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        items: [{
          candidate_id: 'attractions:amap:backup',
          rank: 1,
          score: 91.5,
          place: { name: '庐山植物园', address: '庐山风景区内' },
          recommendation_reasons: ['符合自然景观偏好', '距离当前路线较近'],
          ticket_or_price: {
            currency: 'CNY',
            minimum: 30,
            maximum: 30,
            estimated: false,
          },
        }],
      }),
    })
  })
  await page.route('**/api/v1/trips/trip_wuhan_lushan_demo/patches/preview', async (route) => {
    const request = route.request().postDataJSON()
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'patch_e2e',
        trip_id: 'trip_wuhan_lushan_demo',
        target_type: 'activity',
        target_id: 'new',
        operation: request.operation,
        original_value: {},
        proposed_value: {
          candidate_id: request.candidate_id,
          category: request.category,
          day_id: request.day_id,
          candidate: {
            candidate_id: request.candidate_id,
            rank: 1,
            score: 91.5,
            place: { name: '庐山植物园' },
          },
        },
        impact_scope: [request.day_id],
        time_delta_minutes: 90,
        status: 'preview',
      }),
    })
  })

  await page.goto('/trips/trip_wuhan_lushan_demo/plan')
  await page.getByRole('button', { name: '查看景点备选' }).click()
  await expect(page.getByText('#1 庐山植物园')).toBeVisible()
  await expect(page.getByText('91.5 分')).toBeVisible()
  await page.getByRole('button', { name: '加入' }).click()
  await expect(page.getByText('修改预览')).toBeVisible()
  await expect(page.getByText('正式行程')).toBeVisible()
  await expect(page.getByText('尚未修改')).toBeVisible()
  await expect(page.getByRole('button', { name: '确认应用' })).toBeVisible()
})
