import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: {
          primary: 'var(--bg-primary)',
          secondary: 'var(--bg-secondary)',
          tertiary: 'var(--bg-tertiary)',
        },
        text: {
          primary: 'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
        },
        accent: {
          primary: 'var(--accent-primary)',
          secondary: 'var(--accent-secondary)',
          purple: 'var(--accent-purple)',
        },
        border: {
          default: 'var(--border-default)',
          hover: 'var(--border-hover)',
        },
        status: {
          success: 'var(--status-success)',
          error: 'var(--status-error)',
          warning: 'var(--status-warning)',
          info: 'var(--status-info)',
        },
      },
      borderRadius: {
        sm: '6px',
        md: '10px',
        lg: '16px',
      },
    },
  },
  plugins: [],
} satisfies Config
