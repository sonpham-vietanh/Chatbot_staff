/** @type {import('tailwindcss').Config} */
// Việt Anh Design System — refactor 2026-07 "Học viện Quốc tế": navy #14153A, CTA vàng
// gradient (chữ navy), gold #E8C40A cho điểm nhấn, nền kem #FAF9F4, viền #E7E5DA.
// Font Manrope (chỉ sans-serif). Cỡ chữ tối thiểu 16px — đừng dùng text-xs/text-sm.
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Manrope', 'Arial', 'sans-serif'],
      },
      colors: {
        navy: { DEFAULT: '#14153A', 900: '#0D0E2B', 600: '#26275D', 400: '#565887', 100: '#E2E3F0' },
        gold: { DEFAULT: '#E8C40A', text: '#7A5F00' },
        brand: { yellow: '#F9DD0E' },
        cream: { DEFAULT: '#FAF9F4', hover: '#FBF6DC' },
        line: '#E7E5DA',
        ink: '#1A1A2E',
        muted: '#54556E',
        success: '#1E7F4F',
        danger: '#E03C31',
      },
      borderRadius: { none: '0', sm: '3px', md: '6px', lg: '10px' },
    },
  },
  plugins: [],
}
