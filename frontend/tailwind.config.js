/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Plus Jakarta Sans', 'ui-sans-serif', 'system-ui'],
        display: ['DM Serif Display', 'Georgia', 'serif'],
      },
      colors: {
        ink: '#122735',
        ocean: '#123d52',
        mint: '#dff2e7',
        coral: '#e9795c',
        sand: '#f4efe4',
      },
      boxShadow: {
        soft: '0 18px 60px rgba(18, 39, 53, .10)',
      },
    },
  },
  plugins: [],
}
