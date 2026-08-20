import { useCallback, useEffect, useRef } from 'react'

// Focus management for portalled dialogs (WCAG 2.2 SC 2.4.3 Focus Order,
// with 4.1.2 Name, Role, Value).
//
// Every dialog in this app is rendered with createPortal(…, document.body),
// which appends it AFTER #root. Without the three behaviours below, that is a
// concrete trap rather than a theoretical one:
//
//   1. Focus is never moved in, so a dialog that declares aria-modal="true"
//      removes the rest of the page from the accessibility tree while the
//      user's focus is still sitting in the part that just disappeared — the
//      dialog is announced as nothing and cannot be found.
//   2. Focus is never constrained, so Tab walks out of the dialog and into the
//      dimmed page behind it, where the focus ring travels over blurred,
//      non-actionable content. Because the portal appends last, reaching the
//      dialog's own Close button by tabbing means traversing the entire page
//      first.
//   3. Focus is never restored, so dismissing the dialog drops focus to
//      <body> and the keyboard user loses their place.
//
// Usage: attach the returned ref to the element that should contain focus
// (the dialog panel, not the overlay), and pass `open`. The element needs
// tabIndex={-1} so it can receive focus when it holds no focusable children
// yet. Pass `refocusKey` when the dialog replaces its whole panel while
// staying open, so focus follows the new panel instead of being left on
// <body> (see the focus-move effect).
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export default function useDialogFocus(open, { onEscape, refocusKey } = {}) {
  const containerRef = useRef(null)
  const openerRef = useRef(null)
  // Kept in a ref so a caller passing an inline arrow doesn't re-run the
  // effect (and re-steal focus) on every render.
  const escapeRef = useRef(onEscape)
  useEffect(() => { escapeRef.current = onEscape })

  const focusablesIn = useCallback((node) => {
    if (!node) return []
    return Array.from(node.querySelectorAll(FOCUSABLE)).filter(
      (el) => el.offsetParent !== null || el === document.activeElement,
    )
  }, [])

  // Opener capture and restore, keyed on `open` ALONE. Kept separate from the
  // focus-move effect below so that re-running that effect (a `refocusKey`
  // change, while the dialog is still open) can never overwrite the element we
  // have to hand focus back to on close.
  useEffect(() => {
    if (!open) return undefined
    const opener = document.activeElement
    openerRef.current = opener instanceof HTMLElement ? opener : null
    return () => {
      // Only take focus back if it is still inside (or was dropped by) the
      // dialog — never yank it from somewhere the user deliberately moved to.
      const active = document.activeElement
      if (active === document.body || active == null) {
        openerRef.current?.focus?.()
      }
    }
  }, [open])

  useEffect(() => {
    if (!open) return undefined

    // Move focus in on the next frame: the panel's children mount with it, and
    // for the spotlight tour the panel is repositioned after measurement.
    //
    // `refocusKey` re-runs this for a dialog that swaps its whole panel while
    // staying open. The tour is the live case: card 0 has no anchor and renders
    // the centered-card branch, card 1 anchors to a nav button and renders the
    // spotlight branch. React reconciles those two portals position-by-position,
    // so the first "Continue" press destroys the subtree the button lives in and
    // drops focus to <body> — the exact stranding this hook exists to prevent.
    // Guarded on containment, so a panel that re-renders in place (card 1 → 2,
    // both anchored) leaves the user's place alone instead of yanking them back
    // to the first control on every press.
    const raf = requestAnimationFrame(() => {
      const node = containerRef.current
      if (!node) return
      if (node.contains(document.activeElement)) return
      const first = focusablesIn(node)[0]
      ;(first ?? node).focus?.()
    })

    return () => cancelAnimationFrame(raf)
  }, [open, refocusKey, focusablesIn])

  useEffect(() => {
    if (!open) return undefined

    const onKeyDown = (e) => {
      if (e.key === 'Escape' && escapeRef.current) {
        escapeRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const node = containerRef.current
      if (!node) return
      const items = focusablesIn(node)
      if (items.length === 0) {
        e.preventDefault()
        node.focus?.()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (!node.contains(active)) {
        e.preventDefault()
        ;(e.shiftKey ? last : first).focus()
        return
      }
      if (e.shiftKey && active === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, focusablesIn])

  return containerRef
}
