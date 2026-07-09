/**
 * scrollLock.js — tiny ref-counted body-scroll lock.
 *
 * Multiple UI surfaces (mobile nav drawer, modals) independently want to
 * suppress background scroll while they're open. A naive implementation
 * that does `const prev = document.body.style.overflow; ...; body.style.
 * overflow = prev` breaks the moment two lockers overlap: whichever one
 * closes first "restores" a value that stomps the other's still-active
 * lock (e.g. open the drawer, open a modal on top, close the modal — the
 * modal's restore can wrongly re-enable page scroll even though the
 * drawer is still open).
 *
 * A module-level counter sidesteps that: every caller just increments on
 * lock / decrements on unlock, and `overflow: hidden` is only cleared once
 * the count returns to zero. Safe to call lock()/unlock() any number of
 * times as long as they're paired.
 */

let lockCount = 0

export function lockBodyScroll() {
  lockCount += 1
  if (lockCount === 1) {
    document.body.style.overflow = 'hidden'
  }
}

export function unlockBodyScroll() {
  lockCount = Math.max(0, lockCount - 1)
  if (lockCount === 0) {
    document.body.style.overflow = ''
  }
}
