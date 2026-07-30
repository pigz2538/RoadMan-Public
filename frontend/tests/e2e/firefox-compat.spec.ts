import { expect, test } from '@playwright/test'

test('Firefox loads the optimized interactive vehicle without WebGL failure', async ({ page }) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))

  await page.goto('/')
  const model = page.locator('model-viewer')
  await expect(model).toBeVisible()
  await expect(model).toHaveAttribute('src', '/models/car-concept-optimized.glb')
  await expect(page.getByText('3D 模型已加载')).toBeAttached({ timeout: 30_000 })
  await expect(model).toHaveAttribute('camera-controls')
  await expect(model).not.toHaveAttribute('auto-rotate')
  await expect(page.locator('.vehicle-loading.error')).toHaveCount(0)
  await expect.poll(() => model.evaluate((element) =>
    (element as HTMLElement & { availableVariants: string[] }).availableVariants,
  )).toContain('Pearly Swirly')

  const radius = await model.evaluate((element) =>
    Number.parseFloat((element as HTMLElement & { getCameraOrbit(): { radius: number } }).getCameraOrbit().radius.toString()),
  )
  expect(Number.isFinite(radius)).toBe(true)
  const box = await model.boundingBox()
  if (!box) throw new Error('Firefox 3D canvas did not finish layout')
  await page.mouse.move(box.x + box.width / 2, Math.min(box.y + box.height / 2, 420))
  await page.mouse.wheel(0, -500)
  await page.waitForTimeout(350)
  const zoomedRadius = await model.evaluate((element) =>
    Number.parseFloat((element as HTMLElement & { getCameraOrbit(): { radius: number } }).getCameraOrbit().radius.toString()),
  )
  expect(zoomedRadius).toBeLessThan(radius)
  expect(pageErrors).toEqual([])
})
