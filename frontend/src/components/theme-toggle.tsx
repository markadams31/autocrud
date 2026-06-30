/**
 * theme-toggle.tsx — Header control for the light/dark mode and the accent.
 * The trigger icon reflects the active mode; the menu selects mode + accent.
 */

import { MoonIcon, SunIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ACCENTS, useTheme, type Accent, type Theme } from '@/components/theme-provider'

// The swatch colour shown beside each accent — mirrors the --primary tokens
// defined for each accent in index.css.
const ACCENT_SWATCH: Record<Accent, string> = {
  indigo: 'oklch(0.545 0.196 277)',
  violet: 'oklch(0.55 0.2 300)',
  blue: 'oklch(0.55 0.18 255)',
  emerald: 'oklch(0.6 0.15 162)',
  rose: 'oklch(0.585 0.2 15)',
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export function ThemeToggle() {
  const { theme, setTheme, accent, setAccent } = useTheme()
  const TriggerIcon = theme === 'dark' ? MoonIcon : SunIcon

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon-sm" aria-label="Change theme" />}
      >
        <TriggerIcon />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Mode</DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as Theme)}>
          <DropdownMenuRadioItem value="light">
            <SunIcon />
            Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <MoonIcon />
            Dark
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuLabel>Accent</DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuRadioGroup
          value={accent}
          onValueChange={(value) => setAccent(value as Accent)}
        >
          {ACCENTS.map((name) => (
            <DropdownMenuRadioItem key={name} value={name}>
              <span
                className="size-3.5 rounded-full ring-1 ring-inset ring-black/10"
                style={{ backgroundColor: ACCENT_SWATCH[name] }}
                aria-hidden
              />
              {titleCase(name)}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
