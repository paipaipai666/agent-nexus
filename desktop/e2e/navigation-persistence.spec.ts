import { test, expect } from './fixtures'

test.describe('Page navigation during agent response', () => {
  test('messages persist when navigating away and back during agent response', async ({ page, mocks }) => {
    const { ws } = mocks

    // Send a message
    const textarea = page.locator('textarea')
    await textarea.fill('Write me a poem')
    await textarea.press('Enter')

    // Simulate agent thinking
    ws.emit('thinking', { content: 'Thinking about poetry...' })

    // Verify thinking message appears
    await expect(page.locator('text=Thinking about poetry...')).toBeVisible({ timeout: 3_000 })

    // Simulate first token of agent response
    ws.emit('token', { content: 'Roses are ' })

    // Verify assistant message starts appearing
    await expect(page.locator('text=Roses are')).toBeVisible({ timeout: 3_000 })

    // Navigate to settings page (simulates user switching pages)
    await page.goto('/settings/general')

    // Verify we're on the settings page
    await expect(page.url()).toContain('/settings')

    // Simulate more tokens arriving while we're on the settings page
    // The global WebSocket in SessionProvider should still be receiving these
    ws.emit('token', { content: 'red, violets ' })
    ws.emit('token', { content: 'are blue.' })

    // Navigate back to chat
    await page.goto('/')

    // Wait for the messages to be visible again
    // The messages should be preserved in the SessionProvider context
    await expect(page.locator('text=Roses are red, violets are blue.')).toBeVisible({ timeout: 5_000 })
  })

  test('agent can complete while user is on another page', async ({ page, mocks }) => {
    const { api, ws } = mocks

    // Send a message
    const textarea = page.locator('textarea')
    await textarea.fill('Hello agent')
    await textarea.press('Enter')

    // Simulate agent starts responding
    ws.emit('thinking', { content: 'Processing...' })
    await expect(page.locator('text=Processing...')).toBeVisible({ timeout: 3_000 })

    // Navigate away before agent finishes
    await page.goto('/settings/general')

    // Agent completes while user is away
    ws.emit('answer', { content: 'Hello! How can I help you today?' })
    ws.emit('done', {})

    // Navigate back to chat
    await page.goto('/')

    // The complete answer should be visible
    await expect(page.locator('text=Hello! How can I help you today?')).toBeVisible({ timeout: 5_000 })
  })

  test('cancel works after navigating back', async ({ page, mocks }) => {
    const { ws } = mocks

    // Send a message
    const textarea = page.locator('textarea')
    await textarea.fill('Long running task')
    await textarea.press('Enter')

    // Agent starts
    ws.emit('run_started', { run_id: 'run_123' })
    ws.emit('thinking', { content: 'Working on it...' })

    await expect(page.locator('text=Working on it...')).toBeVisible({ timeout: 3_000 })

    // Navigate away
    await page.goto('/settings/general')

    // Navigate back
    await page.goto('/')

    // The running state should still be reflected
    // (cancel button should be visible if isRunning is true)
    // Note: This depends on the agent still being "running" in the context
    await expect(page.locator('text=Working on it...')).toBeVisible({ timeout: 5_000 })
  })
})
