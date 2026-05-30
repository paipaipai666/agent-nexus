import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Deep dark surfaces
        surface: {
          0: 'var(--surface-0)',     // deepest background
          1: 'var(--surface-1)',     // base surface
          2: 'var(--surface-2)',     // elevated cards
          3: 'var(--surface-3)',     // highest elevation
          4: 'var(--surface-4)',     // hover/active
        },
        // Foreground text
        fg: {
          DEFAULT: 'var(--fg)',
          secondary: 'var(--fg-secondary)',
          muted: 'var(--fg-muted)',
          faint: 'var(--fg-faint)',
        },
        // Accent
        accent: {
          DEFAULT: 'var(--accent)',
          hover: 'var(--accent-hover)',
          muted: 'var(--accent-muted)',
          subtle: 'var(--accent-subtle)',
        },
        // Semantic colors
        green: {
          DEFAULT: 'var(--green)',
          muted: 'var(--green-muted)',
        },
        red: {
          DEFAULT: 'var(--red)',
          muted: 'var(--red-muted)',
        },
        amber: {
          DEFAULT: 'var(--amber)',
          muted: 'var(--amber-muted)',
        },
        blue: {
          DEFAULT: 'var(--blue)',
          muted: 'var(--blue-muted)',
        },
        purple: {
          DEFAULT: 'var(--purple)',
          muted: 'var(--purple-muted)',
        },
        cyan: {
          DEFAULT: 'var(--cyan)',
          muted: 'var(--cyan-muted)',
        },
        // Border
        border: {
          DEFAULT: 'var(--border)',
          subtle: 'var(--border-subtle)',
          strong: 'var(--border-strong)',
        },
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }],  // 10px
        'xs': ['0.75rem', { lineHeight: '1rem' }],         // 12px
        'sm': ['0.8125rem', { lineHeight: '1.125rem' }],   // 13px
        'base': ['0.875rem', { lineHeight: '1.375rem' }],  // 14px
        'lg': ['1rem', { lineHeight: '1.5rem' }],           // 16px
        'xl': ['1.125rem', { lineHeight: '1.625rem' }],    // 18px
        '2xl': ['1.375rem', { lineHeight: '1.875rem' }],   // 22px
      },
      borderRadius: {
        sm: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
      },
      boxShadow: {
        'glow': '0 0 20px var(--accent-muted)',
        'glow-sm': '0 0 10px var(--accent-subtle)',
        'elevated': '0 8px 32px rgba(0,0,0,0.4)',
        'card': '0 1px 3px rgba(0,0,0,0.3), 0 0 0 1px var(--border-subtle)',
      },
      animation: {
        'fade-in': 'fadeIn 0.2s ease-out',
        'slide-up': 'slideUp 0.25s ease-out',
        'slide-down': 'slideDown 0.25s ease-out',
        'scale-in': 'scaleIn 0.15s ease-out',
        'pulse-subtle': 'pulseSubtle 2s ease-in-out infinite',
        'shimmer': 'shimmer 1.5s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideDown: {
          '0%': { opacity: '0', transform: 'translateY(-8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        pulseSubtle: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.7' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
    },
  },
  plugins: [],
} satisfies Config
