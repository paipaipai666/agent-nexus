import { test, expect } from './fixtures'

test.describe('Sidebar session visibility', () => {
  test('shows session in sidebar immediately after first message', async ({ page, mocks }) => {
    const { api, ws } = mocks

    const sidebar = page.locator('nav')
    await expect(sidebar).toBeVisible()

    const sessionButtons = sidebar.locator('button').filter({ hasText: /New session|test message/ })
    await expect(sessionButtons).toHaveCount(0)

    const textarea = page.locator('textarea')
    await textarea.fill('Hello, this is a test message')
    await textarea.press('Enter')

    const sent = await ws.getSentMessages()
    expect(sent.some((m: any) => m.type === 'send_message' && m.content === 'Hello, this is a test message')).toBe(true)

    api.setRecentSessions([
      {
        session_id: 'session_test_1',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        last_message_at: new Date().toISOString(),
        preview: 'Hello, this is a test message',
        profile: null,
      },
    ])

    await expect(sidebar.locator('button').filter({ hasText: 'Hello, this is a test message' })).toBeVisible({ timeout: 5_000 })
  })

  test('sidebar refreshes on session-updated event', async ({ page, mocks }) => {
    const { api, ws } = mocks

    api.setRecentSessions([
      {
        session_id: 'session_old',
        created_at: new Date(Date.now() - 3600000).toISOString(),
        updated_at: new Date(Date.now() - 3600000).toISOString(),
        last_message_at: new Date(Date.now() - 3600000).toISOString(),
        preview: 'Old conversation',
        profile: null,
      },
    ])

    await page.evaluate(() => window.dispatchEvent(new Event('session-updated')))

    api.setRecentSessions([
      {
        session_id: 'session_new',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        last_message_at: new Date().toISOString(),
        preview: 'Brand new conversation',
        profile: null,
      },
      {
        session_id: 'session_old',
        created_at: new Date(Date.now() - 3600000).toISOString(),
        updated_at: new Date(Date.now() - 3600000).toISOString(),
        last_message_at: new Date(Date.now() - 3600000).toISOString(),
        preview: 'Old conversation',
        profile: null,
      },
    ])

    const sidebar = page.locator('nav')
    await expect(sidebar.locator('button').filter({ hasText: 'Brand new conversation' })).toBeVisible({ timeout: 5_000 })
  })
})
