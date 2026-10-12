import { test, expect } from './fixtures'

test('each subagent opens its own timeline and model context', async ({ page, mocks }, testInfo) => {
  mocks.api.setRecentSessions([{ session_id: 's1', question: 'Investigate modules', updated_at: 1 }])
  const makeRows = (agentId: string) => {
    const name = agentId === 'sub_a' ? 'Research A' : 'Research B'
    return [
      { id: 1, run_id: `${agentId}_lifecycle`, seq: 1, ts: 1, event_type: 'run_started', step_id: 0, payload: { name } },
      { id: 2, run_id: `${agentId}_attempt`, seq: 2, ts: 2, event_type: 'turn_journal', step_id: 1,
        payload: { event: 'LLM_CALL', model: `${name} model`, input_tokens: 100, output_tokens: 25, latency_ms: 20 } },
      { id: 3, run_id: `${agentId}_attempt`, seq: 3, ts: 3, event_type: 'tool_start', step_id: 1,
        payload: { name: 'file_read', arguments: { path: `${name}.txt` } } },
    ]
  }
  await page.route('**/api/session/s1/events*', async route => {
    const url = new URL(route.request().url())
    const agentId = url.searchParams.get('agent_id')
    const after = Number(url.searchParams.get('after'))
    const rows = agentId === 'main'
      ? [{ id: 4, run_id: 'parent', seq: 4, ts: 4, event_type: 'tool_start', step_id: 1,
          payload: { name: 'subagent_run', arguments: { task: 'Investigate modules' } } }]
      : url.searchParams.get('view') === 'trajectory'
        ? [{ id: 1, run_id: 'parent', seq: 1, ts: 1, event_type: 'subagent_event', step_id: 0,
            payload: { subagent_id: agentId, name: 'Research A', kind: 'started', status: 'completed', task: 'Read module A', role: 'explorer' } }]
        : agentId ? makeRows(agentId) : []
    const events = rows.filter(row => row.id > after)
    await route.fulfill({ json: { session_id: 's1', events, count: events.length, last_id: events[events.length - 1]?.id ?? after } })
  })
  await page.route('**/api/session/s1/subagents', route => route.fulfill({ json: {
    session_id: 's1', subagents: [{ subagent_id: 'sub_a', name: 'Research A', task: 'Read module A', role: 'explorer', status: 'completed' }],
  } }))
  await page.route('**/api/session/s1/context/**', async route => {
    const url = new URL(route.request().url())
    const agentId = url.searchParams.get('agent_id')
    await route.fulfill({ json: {
      session_id: 's1', run_id: `${agentId}_attempt`, step_id: 1, ts: 1,
      message_count: 2, char_count: 80,
      messages: [{ role: 'system', content: 'Inspect the assigned module.' },
        { role: 'user', content: agentId === 'sub_a' ? 'Module A context' : 'Module B context' }],
    } })
  })

  await page.goto('/chat/s1/timeline')
  await expect(page.getByText('subagent_run {"task":"Investigate modules"}')).toBeVisible()
  await expect(page.getByText('subagent_event', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Research A.txt', { exact: false })).toHaveCount(0)

  await page.goto('/chat/s1/subagent/sub_a')
  await page.getByRole('button', { name: 'Timeline', exact: true }).click()
  await expect(page).toHaveURL(/\/subagent\/sub_a\/timeline$/)
  await expect(page.getByRole('heading', { name: 'Research A Timeline' })).toBeVisible()
  await page.getByRole('button', { name: /LLM call/ }).click()
  await expect(page.getByText('Module A context')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('subagent-timeline-desktop.png') })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Research A Timeline' })).toBeVisible()

  await page.goto('/chat/s1/subagent/sub_b/timeline')
  await expect(page.getByRole('heading', { name: 'Research B Timeline' })).toBeVisible()
  await expect(page.getByText('Research A.txt', { exact: false })).toHaveCount(0)
  await page.getByRole('button', { name: /LLM call/ }).click()
  await expect(page.getByText('Module B context')).toBeVisible()
  await expect(page.getByText('Module A context')).toHaveCount(0)
  await page.setViewportSize({ width: 700, height: 900 })
  await expect(page.getByText('Module B context')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('subagent-timeline-narrow.png') })
})
