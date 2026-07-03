/**
 * SIWE (Sign-In with Ethereum) — EIP-4361 session authentication.
 *
 * After wallet connection, the user signs a challenge message to prove
 * they own the wallet. The backend verifies the signature and issues
 * a session cookie (httpOnly, Secure, SameSite=Strict).
 *
 * This replaces the spoofable X-Wallet-Address header with
 * cryptographically verified wallet ownership.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
// Must match the backend's ARC_CHAIN_ID (auth_siwe.py `_EXPECTED_CHAIN_ID`).
// Configurable so a chain change is a single env flip, not a silent auth break.
const CHAIN_ID = import.meta.env.VITE_ARC_CHAIN_ID ?? '5042002'

/**
 * Perform SIWE authentication: request nonce, sign message, verify.
 *
 * Signing is provider-aware via config.signSiweMessage (#869): EOA wallets
 * personal_sign; Circle passkey wallets sign through their smart account
 * (WebAuthn → ERC-1271, ERC-6492-wrapped when the account is undeployed).
 *
 * @param {string} address — the connected wallet address
 * @returns {Promise<{authenticated: boolean, wallet: string}>}
 */
export async function authenticateWithSIWE(address) {
  // Step 1: Request a nonce from the backend
  const nonceRes = await fetch(`${API_BASE}/api/auth/nonce`)
  if (!nonceRes.ok) throw new Error(`Nonce request failed (${nonceRes.status})`)
  const { nonce, domain, issued_at } = await nonceRes.json()

  // Step 2: Construct the SIWE message (EIP-4361 format)
  const message = [
    `${domain} wants you to sign in with your Ethereum account:`,
    address,
    '',
    'Sign in to Archimedes — prove you own this wallet.',
    '',
    `URI: https://${domain}`,
    `Version: 1`,
    `Chain ID: ${CHAIN_ID}`,
    `Nonce: ${nonce}`,
    `Issued At: ${new Date(issued_at * 1000).toISOString()}`,
  ].join('\n')

  // Step 3: Sign the message (wallet popup — Touch ID or MetaMask confirm)
  const { signSiweMessage } = await import('./config')
  const signature = await signSiweMessage(message)

  // Step 4: Send to backend for verification
  const verifyRes = await fetch(`${API_BASE}/api/auth/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include', // important: include cookies
    body: JSON.stringify({ message, signature }),
  })

  if (!verifyRes.ok) {
    const err = await verifyRes.json().catch(() => ({ detail: 'Verification failed' }))
    throw new Error(err.detail || `Verification failed (${verifyRes.status})`)
  }

  return verifyRes.json()
}

/**
 * Check if the user has an active SIWE session.
 * @returns {Promise<{authenticated: boolean, wallet: string|null}>}
 */
export async function checkSession() {
  try {
    const res = await fetch(`${API_BASE}/api/auth/session`, { credentials: 'include' })
    if (!res.ok) return { authenticated: false, wallet: null }
    return res.json()
  } catch {
    return { authenticated: false, wallet: null }
  }
}

/**
 * Log out — clear the SIWE session cookie.
 */
export async function logout() {
  await fetch(`${API_BASE}/api/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  }).catch(() => {})
}
