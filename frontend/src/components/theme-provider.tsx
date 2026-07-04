/**
 * theme-provider.tsx — Light / dark theming.
 *
 * Applies a `.dark` class to <html> (the CSS already defines `.dark` tokens) and
 * persists the choice. A new visitor with no stored preference starts on their
 * OS preference (resolved once); after that the theme is whatever they pick.
 * A tiny inline script in index.html applies the choice before first paint so
 * there's no flash of the wrong theme on load.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react'

export type Theme = 'light' | 'dark'

/** Accent (brand colour) presets. 'indigo' is the built-in default. */
export const ACCENTS = ['indigo', 'violet', 'blue', 'emerald', 'rose'] as const
export type Accent = (typeof ACCENTS)[number]

const STORAGE_KEY = 'autocrud-theme'
const ACCENT_KEY = 'autocrud-accent'

interface ThemeContextValue {
  theme: Theme
  setTheme: (theme: Theme) => void
  accent: Accent
  setAccent: (accent: Accent) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function initialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  // No stored preference yet — follow the OS this once.
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function initialAccent(): Accent {
  const stored = localStorage.getItem(ACCENT_KEY) as Accent | null
  return stored && ACCENTS.includes(stored) ? stored : 'indigo'
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.style.colorScheme = theme
}

function applyAccent(accent: Accent): void {
  // Always set the attribute, including 'indigo' — it has its own CSS block so
  // the brand colour applies in dark mode too, not just the light-mode default.
  document.documentElement.setAttribute('data-accent', accent)
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(initialTheme)
  const [accent, setAccentState] = useState<Accent>(initialAccent)

  const setTheme = useCallback((next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next)
    setThemeState(next)
  }, [])

  const setAccent = useCallback((next: Accent) => {
    localStorage.setItem(ACCENT_KEY, next)
    setAccentState(next)
  }, [])

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  useEffect(() => {
    applyAccent(accent)
  }, [accent])

  return (
    <ThemeContext.Provider value={{ theme, setTheme, accent, setAccent }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within a ThemeProvider')
  return ctx
}
