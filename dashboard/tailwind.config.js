/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      colors: {
        // OpenAI-inspired neutral palette
        brand: {
          50: '#f0fdf8',
          100: '#ccfbef',
          500: '#10A37F',   // OpenAI teal — primary accent
          600: '#0d8c6d',
          700: '#0a7a5e',
          900: '#0d0d0d',   // Sidebar background (near-black)
        },
        surface: {
          DEFAULT: '#ffffff',
          secondary: '#f7f7f8',
          tertiary: '#ececf1',
        },
      },
      borderRadius: {
        DEFAULT: '10px',
        sm: '6px',
        lg: '16px',
        xl: '16px',
      },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02)',
        elevated: '0 4px 12px rgba(0,0,0,0.08)',
      },
    },
  },
  plugins: [require('@tailwindcss/forms')],
};
