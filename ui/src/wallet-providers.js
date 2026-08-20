// Display labels for the `provider` a wallet link was recorded with.
//
// Deliberately dependency-free (no ./config, no viem): this is pure data +
// one lookup, and keeping it importable outside a browser is what lets the
// node test exercise it directly.
//
// The browser can only ever SEND 'metamask' | 'browser' | 'circle' —
// providerName() in linked-wallets.js emits nothing else. It can READ back
// 'headless', because an API client links wallets against the same account
// and has none of those three (#1293).
export const PROVIDER_LABELS = {
  metamask: 'MetaMask',
  browser: 'Browser wallet',
  circle: 'Circle passkey',
  headless: 'Headless (API)',
}

// Anything unrecognised renders as-is rather than being coerced to a
// wrong-but-familiar label: showing a scripted link as "Browser wallet" is the
// display half of the same untruth the enum change fixed.
export const providerLabel = (provider) => PROVIDER_LABELS[provider] ?? (provider || 'unknown')
