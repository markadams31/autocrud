/**
 * axe.ts — Automated accessibility assertion for component/page tests.
 *
 * Runs axe-core against a rendered subtree and fails the test if any violation
 * is found, scoped to the WCAG 2.0/2.1/2.2 Level A & AA success criteria — the
 * conformance target — rather than axe's broader "best-practice" rules.
 *
 * Note the limits of running axe under jsdom: it has no real layout engine, so
 * rules that depend on rendered geometry or computed colour (notably
 * colour-contrast, and target-size) can't be evaluated and are reported as
 * "incomplete", not "pass". Those need a real browser (e.g. axe via Playwright)
 * or manual review. What this DOES verify reliably is the structural layer —
 * accessible names, roles, ARIA validity, label/control association, and the
 * like — which is exactly where regressions tend to creep in.
 */

import axe from 'axe-core'

const WCAG_A_AA_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

export async function assertNoAxeViolations(node: Element = document.body): Promise<void> {
  const results = await axe.run(node, {
    runOnly: { type: 'tag', values: WCAG_A_AA_TAGS },
  })

  if (results.violations.length > 0) {
    const detail = results.violations
      .map((v) => {
        const nodes = v.nodes.map((n) => `      ${n.target.join(' ')}`).join('\n')
        return `  • [${v.id}] ${v.help}\n    ${v.helpUrl}\n${nodes}`
      })
      .join('\n')
    throw new Error(
      `axe-core found ${results.violations.length} WCAG A/AA violation(s):\n${detail}`,
    )
  }
}
