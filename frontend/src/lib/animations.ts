/**
 * animations.ts — Shared Motion transition presets.
 *
 * Centralising the spring constants keeps motion consistent across the app and
 * makes the "feel" tunable from one place. Springs (not duration easings) are
 * used for anything a user directly triggers — slide-overs, morphs — so the
 * motion has weight and settles naturally rather than stopping abruptly.
 */

import type { Transition, Variants } from 'motion/react'

/**
 * The app's standard ease-out curve for duration-based transitions (row reveal,
 * panel/height fades, the count tween). Springs are preferred for directly-
 * triggered motion; this is for the rest. One curve so the "feel" is consistent.
 */
export const easeOutExpo: [number, number, number, number] = [0.16, 1, 0.3, 1]

/** Snappier spring for small element morphs (save button, icons). */
export const morphSpring: Transition = {
  type: 'spring',
  stiffness: 550,
  damping: 30,
}

/**
 * Staggered list entrance. Put `listContainer` on the wrapper and `listItem`
 * on each child to fade them in one after another — used for the detail panel
 * and the form fields so content arrives with a gentle cascade.
 */
export const listContainer: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.03, delayChildren: 0.03 } },
}

export const listItem: Variants = {
  hidden: { opacity: 0, y: 6 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.28, ease: easeOutExpo },
  },
}

/**
 * Spring for the sidebar's shared active indicator as it slides between table
 * items (via a Motion layoutId). Tuned to feel quick and precise, not bouncy.
 */
export const indicatorSpring: Transition = {
  type: 'spring',
  stiffness: 520,
  damping: 40,
  mass: 0.7,
}
