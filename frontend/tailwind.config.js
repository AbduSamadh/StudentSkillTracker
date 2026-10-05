/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Design tokens: the school's branding is applied per tenant at runtime.
        brand: {
          DEFAULT: 'rgb(var(--brand) / <alpha-value>)',
          ink: 'rgb(var(--brand-ink) / <alpha-value>)',
          soft: 'rgb(var(--brand-soft) / <alpha-value>)',
        },
        accent: 'rgb(var(--accent) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['"Noto Sans"', '"Noto Sans Arabic"', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
