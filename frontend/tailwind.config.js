/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        slate: {
          850: '#141E30',
          900: '#0f172a',
          950: '#020617',
        },
        sky: {
          400: '#38bdf8',
        }
      },
      fontFamily: {
        sans: ['Urbanist', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      }
    },
  },
  plugins: [],
}
