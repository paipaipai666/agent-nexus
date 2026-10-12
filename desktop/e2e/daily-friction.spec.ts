import { test, expect, type Page } from '@playwright/test'
import { installMockWebSocket } from './fixtures'

async function start(page: Page) {
  installMockWebSocket(page)
  await page.addInitScript(() => {
    localStorage.setItem('agentnexus.projects', JSON.stringify({ projects: ['C:/project'], selected: 'C:/project' }))
  })
  const configuration: Record<string, unknown> = {
    cwd: 'C:/project', max_agent_steps: 50, budget_simple_max_tokens: 5000,
    budget_complex_max_tokens: 50000, autocompact_buffer_tokens: 8000,
    shell_enabled: true, shell_confirm: true, shell_timeout: 30, file_read_max_mb: 10,
    post_compact_token_budget: 50000, model_thinking: false, model_thinking_effort: 'medium',
    persona: { agent_name: 'Nexus', identity: '开发搭档', tone: '简洁', projects: [] },
  }
  let sessionNumber = 0
  await page.route(url => url.hostname === '127.0.0.1' && url.port === '18765', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let body: unknown = {}
    if (path === '/health') body = { status: 'ok' }
    else if (path === '/api/config') {
      if (request.method() === 'PUT') {
        const value: unknown = request.postDataJSON()
        if (typeof value === 'object' && value !== null && 'key' in value && typeof value.key === 'string' && 'value' in value) configuration[value.key] = value.value
        body = { status: 'ok' }
      } else body = configuration
    } else if (path.endsWith('/llm/providers')) body = { providers: [], active_model: 'test/model', judge_model: '' }
    else if (path.endsWith('/llm/capabilities')) body = {
      model: 'test/model', base_url: '', source: 'registry', tool_calling: true, json_mode: true,
      json_schema: false, thinking: false, vision: false, parallel_tool_calls: true,
      max_context_tokens: 10000, max_output_tokens: 2000, session_disabled: [],
    }
    else if (path === '/api/session' && request.method() === 'POST') body = { session_id: `new-${++sessionNumber}`, workspace: 'C:/project' }
    else if (path === '/api/session/restore') {
      const value: unknown = request.postDataJSON()
      const id = typeof value === 'object' && value !== null && 'skill' in value && typeof value.skill === 'string' ? value.skill : 'restored'
      body = { session_id: id, restored: true }
    } else if (/^\/api\/session\/[^/]+$/.test(path)) body = { session_id: path.split('/').pop(), workspace: 'C:/project', plan_mode: false, thinking_effort: null }
    else if (path === '/api/sessions/recent') body = { sessions: [], count: 0, has_more: false }
    else if (path.includes('/memory/short')) body = { messages: [], count: 0 }
    else if (path === '/api/runtime/status') body = { model_id: 'test/model', stm_tokens: 0, ctx_max: 10000, total_usage: { input_tokens: 0, output_tokens: 0 }, step_count: 0, context_usage: null, compaction: null }
    else if (path.includes('/skills')) body = { skills: [], count: 0 }
    else if (path.includes('/mcp/tools')) body = { tools: [{ server: 'demo', tool: 'mcp_search_demo', transport: 'stdio' }] }
    else if (path.includes('/mcp/status')) body = { servers: [] }
    else if (path.includes('/version/status')) body = { head: null, can_undo: false, can_redo: false }
    else if (path.includes('/version/log')) body = { checkpoints: [] }
    else if (path.endsWith('/todos')) body = { items: [], count: 0 }
    else if (path.endsWith('/subagents')) body = { subagents: [] }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.goto('/')
  await expect(page.getByRole('textbox', { name: '消息输入' })).toBeVisible()
}

async function openPage(page: Page, name: string) {
  await page.keyboard.press('Control+k')
  await page.getByRole('textbox', { name: '搜索会话、页面或操作' }).fill(name)
  await page.getByRole('button', { name: `打开${name}`, exact: true }).click()
}

async function emit(page: Page, payload: Record<string, unknown>) {
  await page.evaluate(data => {
    const socket = Reflect.get(window, '__mockWsInstance')
    socket.onmessage(new MessageEvent('message', { data: JSON.stringify(data) }))
  }, payload)
}

test.beforeEach(async ({ page }) => { await start(page) })

test('中文输入法确认候选词保留输入，完成组词后可发送', async ({ page }) => {
  const input = page.getByRole('textbox', { name: '消息输入' })
  await input.fill('修复支付报错')
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, bubbles: true })
  await expect(input).toHaveValue('修复支付报错')
  await input.press('Enter')
  await expect.poll(() => page.evaluate(() => Reflect.get(window, '__mockWsSent'))).toEqual(expect.arrayContaining([
    expect.objectContaining({ type: 'send_message', content: '修复支付报错' }),
  ]))
})

test('聊天草稿在页面切换和刷新后保留', async ({ page }) => {
  await page.getByRole('textbox', { name: '消息输入' }).fill('还没有写完的任务')
  await openPage(page, '设置')
  await expect(page.getByRole('heading', { name: '常规设置' })).toBeVisible()
  await openPage(page, '聊天')
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('还没有写完的任务')
  await page.reload()
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('还没有写完的任务')
})

test('创建会话失败保留等待期间编辑的草稿，并显示可恢复的错误', async ({ page }) => {
  let release: () => void = () => { throw new Error('创建请求尚未开始') }
  let started: () => void = () => { throw new Error('等待器尚未设置') }
  const pending = new Promise<void>(resolve => { release = resolve })
  const requested = new Promise<void>(resolve => { started = resolve })
  await page.route('**/api/session', async route => {
    started()
    await pending
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '创建服务暂不可用' }) })
  })
  const input = page.getByRole('textbox', { name: '消息输入' })
  await input.fill('原始任务')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await requested
  await input.fill('补充后的完整任务')
  release()
  await expect(page.getByRole('alert')).toContainText('创建会话失败')
  await expect(input).toHaveValue('补充后的完整任务')
  await expect(page.getByRole('button', { name: '发送消息', exact: true })).toBeEnabled()
})

test('创建会话成功后保留等待期间编辑的草稿，首条消息只发送一次', async ({ page }) => {
  let release: () => void = () => { throw new Error('创建请求尚未开始') }
  let started: () => void = () => { throw new Error('等待器尚未设置') }
  const pending = new Promise<void>(resolve => { release = resolve })
  const requested = new Promise<void>(resolve => { started = resolve })
  await page.route('**/api/session', async route => {
    started()
    await pending
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'created-with-draft', workspace: 'C:/project' }) })
  })
  const input = page.getByRole('textbox', { name: '消息输入' })
  await input.fill('原始任务')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await requested
  await input.fill('后续草稿')
  await input.press('Enter')
  release()
  await expect(page).toHaveURL(/\/chat\/created-with-draft$/)
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('后续草稿')
  await expect.poll(() => page.evaluate(() => {
    const messages: unknown = Reflect.get(window, '__mockWsSent')
    return Array.isArray(messages) ? messages.filter(message => message.type === 'send_message') : []
  })).toEqual([expect.objectContaining({ content: '原始任务' })])
})

test('离开聊天后旧的创建响应不会跳回聊天或发送消息', async ({ page }) => {
  let release: () => void = () => { throw new Error('创建请求尚未开始') }
  let started: () => void = () => { throw new Error('等待器尚未设置') }
  const pending = new Promise<void>(resolve => { release = resolve })
  const requested = new Promise<void>(resolve => { started = resolve })
  await page.route('**/api/session', async route => {
    started()
    await pending
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'abandoned-create', workspace: 'C:/project' }) })
  })
  await page.getByRole('textbox', { name: '消息输入' }).fill('未发送的任务')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await requested
  await openPage(page, '设置')
  await expect(page.getByRole('heading', { name: '常规设置' })).toBeVisible()
  const completed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/session')
  release()
  await completed
  await openPage(page, '聊天')
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('未发送的任务')
  expect(await page.evaluate(() => {
    const messages: unknown = Reflect.get(window, '__mockWsSent')
    return Array.isArray(messages) ? messages.filter(message => message.type === 'send_message') : []
  })).toEqual([])
})

test('会话草稿隔离，新建聊天快捷键不会带入旧内容', async ({ page }) => {
  await page.goto('/chat/one')
  await page.getByRole('textbox', { name: '消息输入' }).fill('会话一草稿')
  await page.goto('/chat/two')
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('')
  await page.getByRole('textbox', { name: '消息输入' }).fill('会话二草稿')
  await page.keyboard.press('Control+n')
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('')
  await page.goto('/chat/one')
  await expect(page.getByRole('textbox', { name: '消息输入' })).toHaveValue('会话一草稿')
})

test('多行输入自动增高，超出上限后保留完整内容', async ({ page }) => {
  const input = page.getByRole('textbox', { name: '消息输入' })
  const before = await input.boundingBox()
  await input.fill(Array.from({ length: 12 }, (_, index) => `第 ${index + 1} 行要求`).join('\n'))
  const after = await input.boundingBox()
  expect(before?.height).toBe(26)
  expect(after?.height).toBe(128)
  await expect(input).toHaveValue(Array.from({ length: 12 }, (_, index) => `第 ${index + 1} 行要求`).join('\n'))
})

test('阅读历史时保持滚动位置，用户可回到最新输出', async ({ page }) => {
  await page.getByRole('textbox', { name: '消息输入' }).fill('输出长文')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByRole('button', { name: '停止任务' })).toBeVisible()
  await emit(page, { type: 'token', content: Array.from({ length: 100 }, (_, i) => `内容 ${i}\n\n`).join('') })
  const scroll = page.getByTestId('chat-scroll')
  await expect(page.getByText('内容 99', { exact: true })).toBeVisible()
  await scroll.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')) })
  await emit(page, { type: 'token', content: '\n\n新的内容' })
  await expect(page.getByRole('button', { name: '回到最新消息' })).toBeVisible()
  expect(await scroll.evaluate(element => element.scrollTop)).toBe(0)
  await page.getByRole('button', { name: '回到最新消息' }).click()
  await expect(page.getByText('新的内容', { exact: true })).toBeInViewport()
})

test('未知命令保留输入，命令菜单不会承诺不存在的 MCP 执行入口', async ({ page }) => {
  const input = page.getByRole('textbox', { name: '消息输入' })
  await input.fill('/mcp_search_demo')
  await expect(page.getByRole('button', { name: /mcp_search_demo/ })).toHaveCount(0)
  await input.press('Enter')
  await expect(input).toHaveValue('/mcp_search_demo')
  await expect(page.getByRole('alert')).toContainText('输入和附件已保留')
  await page.getByRole('button', { name: '作为普通消息发送' }).click()
  await expect.poll(() => page.evaluate(() => Reflect.get(window, '__mockWsSent'))).toEqual(expect.arrayContaining([
    expect.objectContaining({ type: 'send_message', content: '/mcp_search_demo' }),
  ]))
})

test('尚未创建会话时可以查看命令帮助，新建聊天清理临时结果', async ({ page }) => {
  const input = page.getByRole('textbox', { name: '消息输入' })
  await input.fill('/help')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('查看命令帮助')
  await expect(page.getByRole('status')).toContainText('查看已发现的插件')
  await page.keyboard.press('Control+n')
  await expect(input).toHaveValue('')
  await expect(page.getByRole('button', { name: '发送消息', exact: true })).toBeDisabled()
  await expect(page.getByText('今天要完成什么？', { exact: true })).toBeVisible()
  await expect(page.getByText('查看命令帮助', { exact: false })).toHaveCount(0)
})

test('数量设置可读，离开设置后保留草稿，保存后才生效', async ({ page }) => {
  await openPage(page, '设置')
  const budget = page.getByRole('spinbutton', { name: '简单任务 token 预算' })
  await expect(budget).toHaveValue('5000')
  await expect(budget).toHaveAttribute('type', 'number')
  await budget.fill('6000')
  await expect(page.getByText('未保存', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '显示高级设置' }).click()
  const advancedBudget = page.getByRole('spinbutton', { name: '重新载入文件的总预算' })
  await expect(advancedBudget).toHaveValue('50000')
  await advancedBudget.fill('60000')
  await openPage(page, '技能')
  await openPage(page, '设置')
  await expect(budget).toHaveValue('6000')
  await expect(advancedBudget).toHaveValue('60000')
  await page.getByRole('button', { name: '保存更改', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('更改已保存')
  await expect(page.getByRole('button', { name: '保存更改', exact: true })).toBeDisabled()
  await expect(budget).toHaveValue('6000')
})

test('无效数字在字段附近提示，保存失败仍保留待保存内容', async ({ page }) => {
  await openPage(page, '设置')
  const steps = page.getByRole('spinbutton', { name: '任务最大步骤数' })
  await steps.fill('201')
  await page.getByRole('button', { name: '保存更改', exact: true }).click()
  await expect(steps).toHaveAttribute('aria-invalid', 'true')
  await expect(page.getByText('不能大于 200。', { exact: true })).toBeVisible()
  await steps.fill('60')
  await page.route('**/api/config', route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '服务暂不可用' }) }) : route.fallback())
  await page.getByRole('button', { name: '保存更改', exact: true }).click()
  await expect(page.getByRole('alert').first()).toContainText('待保存内容已保留')
  await expect(steps).toHaveValue('60')
  await expect(page.getByRole('button', { name: '保存更改', exact: true })).toBeEnabled()
})

test('能力检测失败显示错误并允许重试', async ({ page }) => {
  let count = 0
  await page.route('**/api/config/llm/capabilities', route => ++count === 1
    ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '能力服务不可用' }) }) : route.fallback())
  await page.getByRole('button', { name: '选择模型', exact: true }).click()
  await expect(page.getByText('检测失败', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重试能力检测' }).click()
  await expect(page.getByText('注册表', { exact: true })).toBeVisible()
  await expect(page.getByText('上下文 10K · 输出 2K')).toBeVisible()
})

test('正文搜索展示预览不含关键词的会话，无结果提示准确', async ({ page }) => {
  await page.route('**/api/sessions/recent*', route => {
    const query = new URL(route.request().url()).searchParams.get('query')
    const sessions = query === 'PaymentTimeoutError' ? [{ session_id: 'archived', preview: '上个月的支付任务', workspace_path: 'C:/project', created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00', last_message_at: '2026-01-01T00:00:00', profile: null }] : []
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions, count: sessions.length, has_more: false }) })
  })
  const search = page.getByRole('textbox', { name: '搜索会话内容', exact: true })
  await search.fill('PaymentTimeoutError')
  await expect(page.getByRole('button', { name: /上个月的支付任务/ })).toBeVisible()
  await search.fill('没有这个关键词')
  await expect(page.getByText('没有匹配的会话，请尝试其他关键词。')).toBeVisible()
})

test('命令面板支持中文搜索、焦点限制和焦点恢复', async ({ page }) => {
  const input = page.getByRole('textbox', { name: '消息输入' })
  await input.focus()
  await page.keyboard.press('Control+k')
  const search = page.getByRole('textbox', { name: '搜索会话、页面或操作' })
  await expect(search).toBeFocused()
  await search.fill('新建聊天')
  await expect(page.getByRole('button', { name: /新建聊天/ }).last()).toBeVisible()
  await page.keyboard.press('Shift+Tab')
  await expect(page.getByRole('dialog', { name: '搜索与命令' }).getByRole('button').last()).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(input).toBeFocused()
  await page.keyboard.press('Control+k')
  await expect(search).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Tab')
  await expect(search).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(input).toBeFocused()
})

test('窄窗口自动收起任务面板，显式打开后可关闭', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 700 })
  await expect(page.getByLabel('任务信息面板')).toHaveCount(0)
  await page.getByRole('button', { name: '查看任务信息', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '任务信息', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭任务信息' }).click()
  await expect(page.getByRole('dialog', { name: '任务信息', exact: true })).not.toBeVisible()
  await expect(page.getByRole('textbox', { name: '消息输入' })).toBeVisible()
})

test('技能操作失败后可以重试，并显示最终启用状态', async ({ page }) => {
  let enabled = true
  let attempts = 0
  await page.route('**/api/skills', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ skills: [{ id: 'demo', display_name: '演示技能', description: '验收用技能', enabled }], count: 1 }) }))
  await page.route('**/api/skills/demo/disable', route => {
    if (++attempts === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '暂不可用' }) })
    enabled = false
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
  })
  await openPage(page, '技能')
  await page.getByRole('switch', { name: '停用技能 演示技能' }).click()
  await expect(page.getByRole('alert')).toContainText('技能停用失败')
  await expect(page.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByRole('switch', { name: '启用技能 演示技能' })).toHaveAttribute('aria-checked', 'false')
  await expect(page.getByText('已启用 0 / 1')).toBeVisible()
})

test('删除失败的重试重新执行删除，并保留失败前的会话', async ({ page }) => {
  let deleted = false
  let attempts = 0
  await page.route('**/api/sessions/recent*', route => {
    const sessions = deleted ? [] : [{ session_id: 'delete-case', preview: '待删除的验收任务', workspace_path: 'C:/project', created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00', last_message_at: '2026-01-01T00:00:00', profile: null }]
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions, count: sessions.length, has_more: false }) })
  })
  await page.route('**/api/sessions', route => {
    if (++attempts === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '删除服务暂不可用' }) })
    deleted = true
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: ['delete-case'], skipped: [], count: 1 }) })
  })
  await page.reload()
  await page.getByRole('button', { name: /待删除的验收任务/ }).hover()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '删除会话', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('删除会话失败')
  await expect(page.getByRole('button', { name: /待删除的验收任务/ })).toBeVisible()
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByRole('button', { name: /待删除的验收任务/ })).toHaveCount(0)
  await expect(page.getByText('暂无会话', { exact: true })).toBeVisible()
})

test('撤销后从服务端刷新历史，不会继续显示旧缓存', async ({ page }) => {
  let undone = false
  await page.route('**/api/memory/short/history*', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [{ role: 'user', content: '原始任务', ts: 1 }, { role: 'assistant', content: undone ? '撤销后的结果' : '撤销前的结果', ts: 2 }], count: 2 }) }))
  await page.route('**/api/version/status*', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'undo-case', head: { id: undone ? 'previous' : 'latest' }, can_undo: !undone, can_redo: undone }) }))
  await page.route('**/api/version/undo*', route => {
    undone = true
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', checkpoint: { id: 'previous' } }) })
  })
  await page.goto('/chat/undo-case')
  await expect(page.getByText('撤销前的结果', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(page.getByText('撤销后的结果', { exact: true })).toBeVisible()
  await expect(page.getByText('撤销前的结果', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重做', exact: true })).toBeEnabled()
})

test('浅深色与窄窗口的设置布局可读，没有水平溢出', async ({ page }) => {
  await openPage(page, '设置')
  for (const width of [1440, 900]) {
    await page.setViewportSize({ width, height: 900 })
    for (const theme of ['深色', '浅色']) {
      const switchTheme = page.getByRole('button', { name: `切换${theme}主题` })
      if (await switchTheme.count()) await switchTheme.click()
      await expect(page.getByRole('heading', { name: '常规设置', exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: '保存更改', exact: true })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await page.screenshot({ path: `../.research_tmp/daily-ux/settings-${width}-${theme}.png` })
    }
  }
})
