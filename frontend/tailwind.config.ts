import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Obsidian & Safety Orange
        obsidian: {
          950: "#08090B",
          900: "#0A0A0C",
          800: "#111113",
          700: "#1A1A1C",
          600: "#232326",
          border: "#232327",
        },
        safety: {
          50: "#FFF3EB",
          100: "#FFE1CC",
          300: "#FF9B52",
          500: "#FF5C00", // primary
          600: "#E05200",
          700: "#B84300",
        },
        critical: {
          DEFAULT: "#FF3B30",
          bg: "rgba(255,59,48,0.08)",
        },
        warning: {
          DEFAULT: "#F5A623",
          bg: "rgba(245,166,35,0.08)",
        },
        info: {
          DEFAULT: "#8B8B93",
          bg: "rgba(139,139,147,0.08)",
        },
        ink: {
          DEFAULT: "#FAFAFA",
          muted: "#A1A1AA",
          dim: "#71717A",
        },
      },
      fontFamily: {
        headline: ["var(--font-geist-sans)", "Geist", "system-ui", "sans-serif"],
        body: ["var(--font-hanken)", "Hanken Grotesk", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "Geist Mono", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        tight2: "-0.02em",
        wide2: "0.08em",
      },
      boxShadow: {
        panel: "0 0 0 1px rgba(255,255,255,0.04)",
        glow: "0 0 24px 2px rgba(255,92,0,0.18)",
      },
      keyframes: {
        "slide-in": {
          "0%": { opacity: "0", transform: "translateY(-8px) scale(0.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        pulse2: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
      },
      animation: {
        "slide-in": "slide-in 0.35s cubic-bezier(0.16,1,0.3,1)",
        pulse2: "pulse2 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
