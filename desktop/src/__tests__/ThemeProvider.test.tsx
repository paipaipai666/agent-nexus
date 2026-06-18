import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ThemeProvider, { useTheme } from '../components/theme/ThemeProvider'

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
    document.documentElement.removeAttribute('data-theme')
  })

  it('defaults to light theme', () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('reads saved theme from localStorage', () => {
    localStorage.setItem('agentnexus-theme', 'dark')

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
  })

  it('toggles theme from light to dark', async () => {
    const user = userEvent.setup()

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')

    await user.click(screen.getByText('toggle'))

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
  })

  it('toggles theme back to light', async () => {
    const user = userEvent.setup()

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    await user.click(screen.getByText('toggle'))
    expect(screen.getByTestId('theme')).toHaveTextContent('dark')

    await user.click(screen.getByText('toggle'))
    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('persists theme to localStorage', async () => {
    const user = userEvent.setup()

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    await user.click(screen.getByText('toggle'))

    expect(localStorage.getItem('agentnexus-theme')).toBe('dark')
  })

  it('sets data-theme attribute on document root', () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('ignores invalid localStorage value', () => {
    localStorage.setItem('agentnexus-theme', 'invalid')

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
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

    expect(screen.getByTestId('deep')).toHaveTextContent('light')
  })
})
