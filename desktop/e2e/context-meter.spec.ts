import { test, expect } from './fixtures'
import type { RuntimeStatus } from '../src/services/api'

const status: RuntimeStatus = {
  model_id: 'test/model', ctx_max: 10000, stm_tokens: 2000, step_count: 87, skill_id: null,
  total_usage: { input_tokens: 123, output_tokens: 45 },
  context_usage: {
    used_tokens: 6000, free_tokens: 4000, measured_at: 1, estimated: true, has_images: false,
    categories: { system_prompt: 1000, tools: 500, mcp: 250, skills: 250, memory: 1000, conversation: 2000, tool_results: 1000 },
  },
  compaction: { basis: 'short_term_memory', used_tokens: 2000, threshold_tokens: 8000 },
}

test('context details remain readable across themes and narrow windows', async ({ page, mocks }, testInfo) => {
  mocks.api.setRecentSessions([])
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/session/restore', route => route.fulfill({ json: { session_id: 's1', restored: true } }))
  await page.route('**/api/runtime/status*', route => route.fulfill({ json: status }))
  await page.goto('/chat/s1')
  await expect(page.getByText('ctx 60.0%')).toBeVisible()
  await expect(page.getByText(/^\d+ steps$/)).toHaveCount(0)
  const trigger = page.getByRole('button', { name: '上下文用量', exact: true })
  await trigger.click()
  const details = page.getByRole('dialog', { name: '上下文用量明细' })
  await expect(details.getByRole('table', { name: '上下文组成' })).toBeVisible()
  await expect(details.getByText('触发阈值 80.0%')).toBeVisible()
  await expect(details.getByText('短期记忆 2,000 / 8,000 tokens')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('context-dark.png') })
  await details.screenshot({ path: testInfo.outputPath('context-panel-dark.png') })
  await page.keyboard.press('Escape')
  await expect(details).toHaveCount(0)
  await expect(trigger).toBeFocused()

  await page.evaluate(() => localStorage.setItem('agentnexus-theme', 'light'))
  await page.reload()
  await page.getByRole('button', { name: '上下文用量', exact: true }).click()
  await expect(page.getByRole('dialog').getByText('System prompt')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('context-light.png') })
  await page.setViewportSize({ width: 700, height: 900 })
  await expect(page.getByRole('dialog').getByText('空余', { exact: true })).toBeVisible()
  const bounds = await page.getByRole('dialog').boundingBox()
  expect(bounds).not.toBeNull()
  if (bounds) {
    expect(bounds.x).toBeGreaterThanOrEqual(0)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(700)
  }
  await page.screenshot({ path: testInfo.outputPath('context-narrow.png') })
  await page.setViewportSize({ width: 380, height: 800 })
  await expect(page.getByRole('button', { name: '上下文用量', exact: true })).toBeInViewport()
  await expect(page.getByRole('dialog').getByText('空余', { exact: true })).toBeVisible()
  const smallBounds = await page.getByRole('dialog').boundingBox()
  expect(smallBounds).not.toBeNull()
  if (smallBounds) expect(smallBounds.x + smallBounds.width).toBeLessThanOrEqual(380)
  expect(errors).toEqual([])
})

test('late context measurements cannot overwrite a different session', async ({ page, mocks }) => {
  mocks.api.setRecentSessions([])
  await page.route('**/api/sessions/recent*', route => route.fulfill({ json: {
    sessions: [
      { session_id: 's1', preview: 'Session A', workspace_path: '/tmp', updated_at: 1 },
      { session_id: 's2', preview: 'Session B', workspace_path: '/tmp', updated_at: 2 },
    ],
  } }))
  await page.route('**/api/session/restore', route => {
    const request: unknown = route.request().postDataJSON()
    const id = typeof request === 'object' && request !== null && 'skill' in request ? request.skill : null
    return route.fulfill({ json: { session_id: id, restored: true } })
  })
  let releaseA = () => {}
  const heldA = new Promise<void>(resolve => { releaseA = resolve })
  let requestedA = () => {}
  const startedA = new Promise<void>(resolve => { requestedA = resolve })
  if (!status.context_usage) throw new Error('Missing test measurement')
  const b: RuntimeStatus = { ...status, ctx_max: 20000, context_usage: { ...status.context_usage, free_tokens: 14000 } }
  await page.route('**/api/runtime/status*', async route => {
    const id = new URL(route.request().url()).searchParams.get('session_id')
    if (id === 's1') {
      requestedA()
      await heldA
      await route.fulfill({ json: status })
    } else {
      await route.fulfill({ json: b })
    }
  })
  await page.goto('/chat/s1')
  await startedA
  await page.getByRole('complementary', { name: 'Chat sessions' }).getByRole('button').filter({ hasText: 'Session B' }).click()
  await expect(page.getByText('ctx 30.0%')).toBeVisible()
  const response = page.waitForResponse(response => response.url().includes('/api/runtime/status?session_id=s1'))
  releaseA()
  await (await response).finished()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await expect(page.getByText('ctx 30.0%')).toBeVisible()
  await expect(page.getByText('ctx 60.0%')).toHaveCount(0)
})
