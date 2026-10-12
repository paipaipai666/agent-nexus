import { beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Titlebar from '../components/titlebar/Titlebar'
import ThemeProvider from '../components/theme/ThemeProvider'
import { useUIStore } from '../services/ui'

describe('Titlebar sidebar control', () => {
  beforeEach(() => {
    cleanup()
    localStorage.clear()
    useUIStore.setState({ sidebarCollapsed: false, paletteOpen: false })
  })

  it('replaces the brand badge with a sidebar button that persists its state', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <ThemeProvider>
          <Titlebar />
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(screen.queryByText('AgentNexus')).not.toBeInTheDocument()
    const hide = screen.getByRole('button', { name: '隐藏侧栏' })
    expect(hide).toHaveAttribute('aria-expanded', 'true')
    await user.click(hide)

    const show = screen.getByRole('button', { name: '显示侧栏' })
    expect(show).toHaveAttribute('aria-expanded', 'false')
    expect(localStorage.getItem('agentnexus-sidebar')).toBe('collapsed')
    await user.click(show)
    expect(screen.getByRole('button', { name: '隐藏侧栏' })).toHaveAttribute('aria-expanded', 'true')
    expect(localStorage.getItem('agentnexus-sidebar')).toBe('expanded')
  })
})
