// Circle Modular Wallets SDK integration — passkey-based smart contract
// accounts (MSCA) on Arc Testnet. Works in any modern browser (Safari,
// Chrome, Firefox) since it uses the standard WebAuthn API instead of an
// injected wallet extension.
//
// Two flows:
//   - Register: first-time user creates a P256 credential on their device
//     (Face ID / Touch ID / Windows Hello / hardware key). The MSCA is
//     derived deterministically from the public key.
//   - Login: returning user re-authenticates with the same credential to
//     unlock the same MSCA address.
//
// MSCAs are LAZILY DEPLOYED — gas for account creation is deferred until
// the first outbound user operation. The Arc Testnet path is `/arcTestnet`.
//
// Reference: submodules/context-arc/docs/circlefin-skills/use-modular-wallets.md
// Setup: ui/.env.example documents VITE_CIRCLE_CLIENT_KEY + VITE_CIRCLE_CLIENT_URL.

import { createPublicClient } from 'viem'
import { toWebAuthnAccount } from 'viem/account-abstraction'
import {
  toWebAuthnCredential,
  toPasskeyTransport,
  toModularTransport,
  toCircleSmartAccount,
  WebAuthnMode,
} from '@circle-fin/modular-wallets-core'

const CLIENT_KEY = import.meta.env.VITE_CIRCLE_CLIENT_KEY ?? ''
const CLIENT_URL = import.meta.env.VITE_CIRCLE_CLIENT_URL
  ?? 'https://modular-sdk.circle.com/v1/rpc/w3s/buidl'

// Arc Testnet chain definition; matches the EOA-path chain in config.js so
// any downstream code that introspects .chain sees the same object shape.
const arcTestnet = {
  id: 5042002,
  name: 'Arc Testnet',
  nativeCurrency: { name: 'USD Coin', symbol: 'USDC', decimals: 18 },
  rpcUrls: { default: { http: ['https://rpc.testnet.arc.network'] } },
}

// Demo-grade persistence. Per Circle docs, production should use httpOnly
// cookies to mitigate XSS credential theft — out of scope for hackathon demo.
const CREDENTIAL_STORAGE_KEY = 'archimedes_circle_credential'
// Per-device username so Circle's server-side uniqueness check doesn't lock
// out everyone after the first registrant (see issue #367). Generated on
// first use; same key reused across sessions so MSCA address stays stable.
const USERNAME_STORAGE_KEY = 'archimedes_circle_username'

// True if a CLIENT_KEY was supplied at build time. The modal hides the
// passkey option when this is false so we never show a broken button.
export function circlePasskeyEnabled() {
  return Boolean(CLIENT_KEY)
}

function loadStoredCredential() {
  try {
    const raw = localStorage.getItem(CREDENTIAL_STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function saveCredential(credential) {
  try {
    localStorage.setItem(CREDENTIAL_STORAGE_KEY, JSON.stringify(credential))
  } catch { /* storage unavailable */ }
}

// Returns the per-device username, generating + persisting one on first use.
// Format: `archimedes-<8 hex chars>` — within Circle's 5-50 char and
// [a-zA-Z0-9_@.:+-] constraints; 8 hex = 4 billion namespaces, collision-free
// in practice across a hackathon team and any number of judges.
function getOrCreateUsername() {
  try {
    const existing = localStorage.getItem(USERNAME_STORAGE_KEY)
    if (existing) return existing
    const suffix = (crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`)
      .replace(/-/g, '')
      .slice(0, 8)
    const fresh = `archimedes-${suffix}`
    localStorage.setItem(USERNAME_STORAGE_KEY, fresh)
    return fresh
  } catch {
    return `archimedes-${Date.now()}`
  }
}

export function clearCircleSession() {
  try {
    localStorage.removeItem(CREDENTIAL_STORAGE_KEY)
    localStorage.removeItem(USERNAME_STORAGE_KEY)
  } catch { /* */ }
}

// Returns true if a previously-registered passkey is on file for this
// origin. Cheap synchronous check used to decide whether the modal should
// show "Sign in with Passkey" (returning) vs "Create Passkey" (new user).
export function hasStoredPasskey() {
  return loadStoredCredential() !== null
}

// Map raw passkey errors (viem RPC errors, DOMExceptions) to user-readable
// strings. The default error.message for viem is "An unknown RPC error
// occurred. Details: …. Version: viem@x.y.z" — informative for debugging,
// hostile for the user. This converts the common cases.
function friendlyPasskeyError(err) {
  const msg = String(err?.message ?? err ?? '')
  if (/NotAllowedError/i.test(msg) || /not allowed/i.test(msg)) {
    return 'Sign-in cancelled or no passkey selected.'
  }
  if (/SecurityError/i.test(msg)) {
    return 'Passkey blocked: this site\'s domain doesn\'t match the registered passkey origin.'
  }
  if (/username is duplicated/i.test(msg)) {
    return 'A passkey for this site already exists on Circle\'s server but no passkey matched on your device. Use the device that originally created it, or click "Clear and try again" to register fresh.'
  }
  if (/no credentials available/i.test(msg) || /no passkey/i.test(msg)) {
    return 'No passkey found on this device. Create a new one to continue.'
  }
  return msg || 'Passkey sign-in failed.'
}

// Single entry point for both flows. The caller passes `mode = 'register'`
// for a new user; we default to 'login' if we already have a stored
// credential. Returns the smart account + its address + the credential
// (also persisted to localStorage for next-session re-login).
//
// Throws standard WebAuthn DOMException errors on user cancellation
// (NotAllowedError) or domain mismatch (SecurityError) — caller should
// catch and surface a friendly message.
// Username constraint per Circle API: 5-50 chars, [a-zA-Z0-9_@.:+-]+ only.
// Spaces are rejected; 'Archimedes user' (the old default) failed every
// first-time signup with "The username is invalid."
// Username is per-device (see getOrCreateUsername) so Circle's server-side
// uniqueness check doesn't collide across teammates / judges — issue #367.
export async function connectCirclePasskey({ mode = 'auto', username } = {}) {
  if (!circlePasskeyEnabled()) {
    throw new Error('Circle passkey wallet is not configured (missing VITE_CIRCLE_CLIENT_KEY).')
  }

  const resolvedUsername = username ?? getOrCreateUsername()
  const stored = loadStoredCredential()
  let resolvedMode = mode === 'auto'
    ? (stored ? WebAuthnMode.Login : WebAuthnMode.Register)
    : (mode === 'login' ? WebAuthnMode.Login : WebAuthnMode.Register)

  // Passkey transport handles WebAuthn challenge issuance + verification.
  const passkeyTransport = toPasskeyTransport(CLIENT_URL, CLIENT_KEY)

  // Either issue a new P256 credential (Register) OR re-authenticate an
  // existing one (Login). Browser prompts the user for biometrics here.
  let credential
  try {
    credential = await toWebAuthnCredential({
      transport: passkeyTransport,
      mode: resolvedMode,
      username: resolvedUsername,
      credentialId: resolvedMode === WebAuthnMode.Login ? stored?.id : undefined,
    })
  } catch (err) {
    // "Username is duplicated" — Circle server already has a credential for
    // this username, but our localStorage is empty (typical: Safari ITP
    // purged it, or user switched browsers). Retry as Login WITHOUT a
    // credentialId — WebAuthn surfaces the user's existing passkeys for
    // this origin via discoverable credentials, and the user re-auths.
    // Only auto-retry when the caller didn't pin a specific mode.
    const msg = String(err?.message ?? err ?? '')
    const isDuplicate = /username is duplicated/i.test(msg)
    if (mode === 'auto' && resolvedMode === WebAuthnMode.Register && isDuplicate) {
      resolvedMode = WebAuthnMode.Login
      try {
        credential = await toWebAuthnCredential({
          transport: passkeyTransport,
          mode: WebAuthnMode.Login,
          username: resolvedUsername,
        })
      } catch (retryErr) {
        throw new Error(friendlyPasskeyError(retryErr), { cause: retryErr })
      }
    } else {
      throw new Error(friendlyPasskeyError(err), { cause: err })
    }
  }

  // Persist for next-session login. Stores the credential ID + public key,
  // NOT the private key (the private key lives in the device's secure
  // enclave and never leaves it).
  saveCredential(credential)

  // Modular transport handles bundler RPC + chain-specific calls for the
  // smart account on Arc Testnet.
  const modularTransport = toModularTransport(`${CLIENT_URL}/arcTestnet`, CLIENT_KEY)
  const client = createPublicClient({ chain: arcTestnet, transport: modularTransport })

  // Derive a WebAuthnAccount viem account from the credential, then turn
  // it into a Circle smart account. The MSCA address is deterministic
  // from the credential's public key — same passkey → same address.
  const owner = toWebAuthnAccount({ credential })
  const smartAccount = await toCircleSmartAccount({ client, owner })

  return {
    address: smartAccount.address,
    smartAccount,
    client,
    credential,
    mode: resolvedMode,
  }
}

// Rebuild the smart account from a previously-stored credential WITHOUT
// triggering a WebAuthn prompt. Safe to call on every page load — the
// credential only contains the public key (the private key lives in the
// device's secure enclave) so we can derive the smart account address
// + signer wrapper without re-authenticating. The user only sees a
// WebAuthn prompt when they actually try to sign a user operation.
//
// Returns null if there's no stored credential (caller should redirect
// to the register flow).
export async function rehydrateSmartAccount() {
  if (!circlePasskeyEnabled()) return null
  const credential = loadStoredCredential()
  if (!credential) return null

  const modularTransport = toModularTransport(`${CLIENT_URL}/arcTestnet`, CLIENT_KEY)
  const client = createPublicClient({ chain: arcTestnet, transport: modularTransport })

  const owner = toWebAuthnAccount({ credential })
  const smartAccount = await toCircleSmartAccount({ client, owner })

  return {
    address: smartAccount.address,
    smartAccount,
    client,
    credential,
  }
}
