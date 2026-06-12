import { test, expect } from './fixtures'

test.describe('Sidebar session visibility', () => {
  test('shows session in sidebar immediately after first message', async ({ page, mocks }) => {
    const { api, ws } = mocks

    // Initially sidebar should show no recent sessions
    const sidebar = page.locator('nav')
    await expect(sidebar).toBeVisible()

    // Verify no session items in sidebar initially
    const sessionButtons = sidebar.locator('button').filter({ hasText: /New session|test message/ })
    await expect(sessionButtons).toHaveCount(0)

    // Type and send a message
    const textarea = page.locator('textarea')
    await textarea.fill('Hello, this is a test message')
    await textarea.press('Enter')

    // The app should have sent the message via WS
    const sent = await ws.getSentMessages()
    expect(sent.some((m: any) => m.type === 'send_message' && m.content === 'Hello, this is a test message')).toBe(true)

    // Simulate the backend returning the session in recent sessions
    // (the session-updated event triggers a sidebar refresh)
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

    // Wait for the sidebar to show the session
    // The sidebar refreshes on 'session-updated' event
    await expect(sidebar.locator('button').filter({ hasText: 'Hello, this is a test message' })).toBeVisible({ timeout: 5_000 })
  })

  test('sidebar refreshes on session-updated event', async ({ page, mocks }) => {
    const { api, ws } = mocks

    // Set up initial sessions
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

    // Trigger a session-updated event (simulating what sendMessage does)
    await page.evaluate(() => window.dispatchEvent(new Event('session-updated')))

    // Update the mock to return a new session
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

    // Wait for sidebar to refresh and show the new session
    const sidebar = page.locator('nav')
    await expect(sidebar.locator('button').filter({ hasText: 'Brand new conversation' })).toBeVisible({ timeout: 5_000 })
  })
})
