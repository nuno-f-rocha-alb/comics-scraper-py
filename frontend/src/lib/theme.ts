// Theme: .dark class on <html>, persisted in localStorage 'cs-theme', default = system.
export type Theme = "light" | "dark"

const KEY = "cs-theme"

export function getInitialTheme(): Theme {
  const saved = localStorage.getItem(KEY) as Theme | null
  if (saved === "light" || saved === "dark") return saved
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark")
  localStorage.setItem(KEY, theme)
}
