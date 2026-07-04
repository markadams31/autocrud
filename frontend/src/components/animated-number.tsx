/**
 * animated-number.tsx — A number that tweens to its new value.
 *
 * Used for the record count, so it ticks rather than jumps when a filter,
 * delete, or create changes the total. Built on Motion values, so it animates
 * off the main React render loop and honours the OS "reduce motion" setting via
 * the app-level MotionConfig.
 */

import { useEffect } from 'react'
import { animate, motion, useMotionValue, useTransform } from 'motion/react'

import { easeOutExpo } from '@/lib/animations'

const format = new Intl.NumberFormat()

export function AnimatedNumber({ value, className }: { value: number; className?: string }) {
  const motionValue = useMotionValue(value)
  const text = useTransform(motionValue, (v) => format.format(Math.round(v)))

  useEffect(() => {
    const controls = animate(motionValue, value, { duration: 0.4, ease: easeOutExpo })
    return controls.stop
  }, [value, motionValue])

  return <motion.span className={className}>{text}</motion.span>
}
