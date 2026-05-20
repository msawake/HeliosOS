/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Ported from mission-control.py inline CSS variables.
        bg: "#0d1117",
        surface: "#161b22",
        border: "#30363d",
        text: "#c9d1d9",
        dim: "#8b949e",
        bright: "#f0f6fc",
        ok: "#3fb950",
        danger: "#f85149",
        warn: "#d29922",
        info: "#58a6ff",
        orange: "#db6d28",
        purple: "#bc8cff",
        cyan: "#39d353",
        pink: "#f778ba",
        // shadcn token aliases (dark theme baseline)
        background: "#0d1117",
        foreground: "#c9d1d9",
        muted: { DEFAULT: "#161b22", foreground: "#8b949e" },
        card: { DEFAULT: "#161b22", foreground: "#c9d1d9" },
        primary: { DEFAULT: "#58a6ff", foreground: "#0d1117" },
        secondary: { DEFAULT: "#21262d", foreground: "#c9d1d9" },
        destructive: { DEFAULT: "#f85149", foreground: "#ffffff" },
        accent: { DEFAULT: "#21262d", foreground: "#c9d1d9" },
        popover: { DEFAULT: "#161b22", foreground: "#c9d1d9" },
        input: "#30363d",
        ring: "#58a6ff",
      },
      fontFamily: {
        mono: ["'SF Mono'", "'Cascadia Code'", "'Fira Code'", "monospace"],
      },
      borderRadius: { lg: "6px", md: "4px", sm: "3px" },
    },
  },
  plugins: [],
};
