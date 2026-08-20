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
// yet.
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export default function useDialogFocus(open, { onEscape } = {}) {
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

  useEffect(() => {
    if (!open) return undefined
    const opener = document.activeElement
    openerRef.current = opener instanceof HTMLElement ? opener : null

    // Move focus in on the next frame: the panel's children mount with it, and
    // for the spotlight tour the panel is repositioned after measurement.
    const raf = requestAnimationFrame(() => {
      const node = containerRef.current
      if (!node) return
      const first = focusablesIn(node)[0]
      ;(first ?? node).focus?.()
    })

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
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', onKeyDown, true)
      // Only take focus back if it is still inside (or was dropped by) the
      // dialog — never yank it from somewhere the user deliberately moved to.
      const active = document.activeElement
      if (active === document.body || active == null) {
        openerRef.current?.focus?.()
      }
    }
  }, [open, focusablesIn])

  return containerRef
}
