/** @type {import('tailwindcss').Config} */
// Màu và font theo Brand Guideline Trường Việt Anh v2.0 (docs/Brand-Guideline-Viet-Anh-MASTER.docx):
// Navy #26275D chủ đạo + Vàng #F9DD0E chỉ cho CTA/điểm nhấn; nền #F0F4F8; chữ #1A1A2E; viền #E2E8F0.
// Font Be Vietnam Pro (chỉ sans-serif). Cỡ chữ tối thiểu 16px — đừng dùng text-xs/text-sm.
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Be Vietnam Pro"', 'Arial', 'sans-serif'],
      },
      colors: {
        navy: { DEFAULT: '#26275D', dark: '#1B1C45', soft: '#E9EAF3' },
        brand: { yellow: '#F9DD0E', 'yellow-dark': '#E5C900' },
        surface: '#F0F4F8',
        ink: '#1A1A2E',
        line: '#E2E8F0',
        muted: '#555873',
        danger: '#B42318',
        success: '#1E7A4C',
      },
      boxShadow: {
        card: '0 1px 2px rgba(38, 39, 93, .06), 0 8px 24px rgba(38, 39, 93, .06)',
      },
    },
  },
  plugins: [],
}
