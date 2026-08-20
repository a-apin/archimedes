const STORAGE_KEY = 'archimedes.theme'

// localStorage can throw (Safari ITP in an embedded/third-party context,
// Chrome "block all cookies", an enterprise policy, a privacy extension).
// getStoredTheme runs as the lazy useState initializer on the render path of
// every /app page — including the anonymous-OK front doors (#1357) — so an
// uncaught throw here unmounts the whole React root. Fail safe to the
// default theme instead.
export function getStoredTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applyTheme(theme) {
  // Must run unconditionally, outside the try below: a storage-blocked
  // visitor still gets the theme applied for this session. Only the persist
  // (localStorage.setItem, which can throw for the same reasons as above)
  // is guarded — letting it throw uncaught here previously ran the DOM
  // update and then threw, so the caller's setState never ran (#1357).
  document.documentElement.setAttribute('data-theme', theme)
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Non-fatal: the theme just won't persist across reloads for this visitor.
  }
}
