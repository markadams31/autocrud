/**
 * confetti.tsx — A little burst of joy on a successful write.
 *
 * Provides a `celebrate()` function via context; calling it fires a party-popper
 * confetti burst from a point (defaults to upper-centre). Particles launch in an
 * upward fan, then gravity rains them down as they spin and fade. Built on Motion
 * so it automatically calms down under the OS "reduce motion" setting.
 *
 * No dependency — the particles use the app's own accent palette, so the
 * celebration feels of-a-piece with the rest of the UI rather than bolted on.
 */

import { createContext, useCallback, useContext, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'

type Origin = { x: number; y: number }

const CelebrateContext = createContext<(origin?: Origin) => void>(() => {})

/** Fire a confetti burst. Pass an origin in viewport pixels, or omit for centre. */
export function useCelebrate() {
  return useContext(CelebrateContext)
}

// Brand-forward, festive palette: indigo/violet lead, with bright pops.
const COLORS = ['#6366f1', '#8b5cf6', '#a855f7', '#ec4899', '#10b981', '#f59e0b', '#0ea5e9']

const PARTICLES = 38

interface Particle {
  dx: number
  dy: number
  gravity: number
  drift: number
  color: string
  size: number
  rounded: boolean
  rotate: number
  duration: number
  delay: number
}

function makeParticles(): Particle[] {
  return Array.from({ length: PARTICLES }, () => {
    // Launch in an upward fan (screen y grows downward, so "up" is negative).
    const angle = -Math.PI / 2 + (Math.random() - 0.5) * 2.3
    const speed = 130 + Math.random() * 170
    return {
      dx: Math.cos(angle) * speed,
      dy: Math.sin(angle) * speed,
      gravity: 280 + Math.random() * 220,
      drift: (Math.random() - 0.5) * 60,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      size: 6 + Math.random() * 6,
      rounded: Math.random() > 0.5,
      rotate: (Math.random() - 0.5) * 720,
      duration: 0.9 + Math.random() * 0.6,
      delay: Math.random() * 0.06,
    }
  })
}

function Burst({ x, y }: Origin) {
  // Particle layout is fixed for the life of the burst.
  const [parts] = useState(makeParticles)
  return (
    <div className="absolute" style={{ left: x, top: y }}>
      {parts.map((p, i) => (
        <motion.span
          key={i}
          className="absolute block"
          style={{
            width: p.size,
            height: p.size,
            backgroundColor: p.color,
            borderRadius: p.rounded ? '9999px' : '2px',
          }}
          initial={{ x: 0, y: 0, opacity: 1, scale: 0.5, rotate: 0 }}
          animate={{
            x: [0, p.dx, p.dx + p.drift],
            y: [0, p.dy, p.dy + p.gravity],
            opacity: [1, 1, 0],
            scale: [0.5, 1, 0.85],
            rotate: [0, p.rotate],
          }}
          transition={{ duration: p.duration, delay: p.delay, ease: [0.18, 0.7, 0.3, 1] }}
        />
      ))}
    </div>
  )
}

interface ActiveBurst extends Origin {
  id: number
}

export function CelebrationProvider({ children }: { children: React.ReactNode }) {
  const [bursts, setBursts] = useState<ActiveBurst[]>([])
  const nextId = useRef(0)

  const celebrate = useCallback((origin?: Origin) => {
    const x = origin?.x ?? window.innerWidth / 2
    const y = origin?.y ?? window.innerHeight * 0.38
    const id = nextId.current++
    setBursts((b) => [...b, { id, x, y }])
    window.setTimeout(() => {
      setBursts((b) => b.filter((burst) => burst.id !== id))
    }, 1800)
  }, [])

  return (
    <CelebrateContext.Provider value={celebrate}>
      {children}
      <div className="pointer-events-none fixed inset-0 z-[100] overflow-hidden">
        <AnimatePresence>
          {bursts.map((b) => (
            <Burst key={b.id} x={b.x} y={b.y} />
          ))}
        </AnimatePresence>
      </div>
    </CelebrateContext.Provider>
  )
}
