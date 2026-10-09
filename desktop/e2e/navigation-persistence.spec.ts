import { test, expect } from './fixtures'

test.describe('Page navigation during agent response', () => {
  test('messages persist when navigating away and back during agent response', async ({ page, mocks }) => {
    const { ws } = mocks

    const textarea = page.locator('textarea')
    await textarea.fill('Write me a poem')
    await textarea.press('Enter')

    ws.emit('thinking', { content: 'Thinking about poetry...' })

    await expect(page.locator('text=Thinking about poetry...')).toBeVisible({ timeout: 3_000 })

    ws.emit('token', { content: 'Roses are ' })

    await expect(page.locator('text=Roses are')).toBeVisible({ timeout: 3_000 })

    await page.goto('/settings/general')

    await expect(page.url()).toContain('/settings')

    ws.emit('token', { content: 'red, violets ' })
    ws.emit('token', { content: 'are blue.' })

    await page.goto('/')

    await expect(page.locator('text=Roses are red, violets are blue.')).toBeVisible({ timeout: 5_000 })
  })

  test('agent can complete while user is on another page', async ({ page, mocks }) => {
    const { api, ws } = mocks

    const textarea = page.locator('textarea')
    await textarea.fill('Hello agent')
    await textarea.press('Enter')

    ws.emit('thinking', { content: 'Processing...' })
    await expect(page.locator('text=Processing...')).toBeVisible({ timeout: 3_000 })

    await page.goto('/settings/general')

    ws.emit('answer', { content: 'Hello! How can I help you today?' })
    ws.emit('done', {})

    await page.goto('/')

    await expect(page.locator('text=Hello! How can I help you today?')).toBeVisible({ timeout: 5_000 })
  })

  test('cancel works after navigating back', async ({ page, mocks }) => {
    const { ws } = mocks

    const textarea = page.locator('textarea')
    await textarea.fill('Long running task')
    await textarea.press('Enter')

    ws.emit('run_started', { run_id: 'run_123' })
    ws.emit('thinking', { content: 'Working on it...' })

    await expect(page.locator('text=Working on it...')).toBeVisible({ timeout: 3_000 })

    await page.goto('/settings/general')

    await page.goto('/')

    await expect(page.locator('text=Working on it...')).toBeVisible({ timeout: 5_000 })
  })
})
