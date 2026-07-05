import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        base: "#0b0e13",
        sidebar: "#0e1218",
        card: "#141a22",
        tile: "#10151b",
        border: "#1c222b",
        "text-primary": "#eef2f5",
        "text-section": "#c7cfd6",
        "text-body": "#aab4bd",
        "text-muted": "#5b6673",
        "text-label": "#8b96a3",
        mint: "#5eead4",
        "mint-dim": "#5eead41a",
        green: "#6fce8f",
        "green-dim": "#6fce8f22",
        amber: "#e8963f",
        "amber-dim": "#e8963f22",
        red: "#e05d5d",
        "red-dim": "#e05d5d22",
        blue: "#7ea6f5",
        "blue-dim": "#5b8def22",
        "gray-badge": "#9aa2ab",
        "gray-badge-bg": "#2a2a2a",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      borderRadius: {
        card: "14px",
        tile: "10px",
        badge: "5px",
        "badge-pill": "20px",
      },
    },
  },
  plugins: [],
};

export default config;
