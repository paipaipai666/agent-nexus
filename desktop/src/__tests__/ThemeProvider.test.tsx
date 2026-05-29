import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ThemeProvider, { useTheme } from '../components/theme/ThemeProvider'
import { useEffect } from 'react'

function ThemeConsumer() {
  const { theme, toggleTheme } = useTheme()
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <button onClick={toggleTheme}>toggle</button>
    </div>
  )
}

describe('ThemeProvider', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark', 'light')
  })

  it('defaults to dark theme', () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
  })

  it('reads saved theme from localStorage', () => {
    localStorage.setItem('theme', 'light')

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('toggles theme from dark to light', async () => {
    const user = userEvent.setup()

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')

    await user.click(screen.getByText('toggle'))

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('toggles theme back to dark', async () => {
    const user = userEvent.setup()

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    await user.click(screen.getByText('toggle'))
    expect(screen.getByTestId('theme')).toHaveTextContent('light')

    await user.click(screen.getByText('toggle'))
    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
  })

  it('persists theme to localStorage', async () => {
    const user = userEvent.setup()

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    await user.click(screen.getByText('toggle'))

    expect(localStorage.getItem('theme')).toBe('light')
  })

  it('adds theme class to document root', () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('ignores invalid localStorage value', () => {
    localStorage.setItem('theme', 'invalid')

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
  })

  it('provides context to nested components', () => {
    function DeepChild() {
      const { theme } = useTheme()
      return <span data-testid="deep">{theme}</span>
    }

    render(
      <ThemeProvider>
        <div>
          <DeepChild />
        </div>
      </ThemeProvider>
    )

    expect(screen.getByTestId('deep')).toHaveTextContent('dark')
  })
})
