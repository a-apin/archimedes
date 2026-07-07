import { createPublicClient, createWalletClient, custom, http } from 'viem'
import {
  connectCirclePasskey,
  clearCircleSession,
  circlePasskeyEnabled,
  rehydrateSmartAccount,
} from './circle-wallet'

const arcTestnet = {
  id: 5042002,
  name: 'Arc Testnet',
  nativeCurrency: { name: 'USD Coin', symbol: 'USDC', decimals: 18 },
  rpcUrls: { default: { http: ['https://rpc.testnet.arc.network'] } },
}

export const publicClient = createPublicClient({
  chain: arcTestnet,
  transport: http(),
})

// ─── Wallet Connection ──────────────────────────────────────
//
// Discovery follows EIP-6963 (Multi Injected Provider Discovery) when the
// wallet supports it; falls back to legacy window.ethereum sniffing for
// older wallets. EIP-6963 is the modern standard — newer Coinbase Wallet,
// Rabby, Brave, Phantom EVM, etc. only announce themselves this way.
// Reference: https://eips.ethereum.org/EIPS/eip-6963

// Keyed by rdns (reverse-DNS identifier the wallet self-declares).
const eip6963Providers = new Map()

// ── Live account/chain-change listeners (EIP-6963-aware) ──────────────────
// Providers we've already bound change-listeners to. WeakSet dedups so a
// re-announcement doesn't stack duplicate handlers, and lets GC reclaim
// providers we never keep.
const _listenerBoundProviders = new WeakSet()
let _lastSeenChainId = null

// Bound per-provider so the ACTIVE wallet drives session state regardless of
// whether it is window.ethereum (injected EOA) or an EIP-6963 provider
// (Rabby/Brave/…) whose object is NOT window.ethereum (#921). Every handler
// no-ops unless the event's provider is the one connectWallet selected
// (_provider), so an announced-but-inactive wallet can't hijack the session.
function _onAccountsChanged(provider, accounts) {
  if (provider !== _provider) return
  if (!accounts?.length) {
    disconnectWallet()
    window.dispatchEvent(new CustomEvent('wallet-changed', { detail: { address: null } }))
    return
  }
  _address = accounts[0]
  if (_providerId) saveWalletMeta(_providerId, _address)
  _walletClient = createWalletClient({ account: _address, chain: arcTestnet, transport: custom(provider) })
  window.dispatchEvent(new CustomEvent('wallet-changed', { detail: { address: _address } }))
}

function _onChainChanged(provider, newChainId) {
  if (provider !== _provider) return
  // Coinbase Wallet (Chrome) re-emits chainChanged on internal lifecycle
  // events (tab sync, popup re-open) even when the chain hasn't changed; the
  // previous handler reloaded on every emit → infinite reload loop. Track the
  // last-seen chain id and react only to actual transitions. viem clients are
  // pinned to arcTestnet at construction, so no rebuild is needed — just notify.
  if (newChainId === _lastSeenChainId) return
  _lastSeenChainId = newChainId
  window.dispatchEvent(new CustomEvent('wallet-chain-changed', { detail: { chainId: newChainId } }))
}

function attachWalletListeners(provider) {
  if (!provider?.on || _listenerBoundProviders.has(provider)) return
  _listenerBoundProviders.add(provider)
  provider.on('accountsChanged', (accounts) => _onAccountsChanged(provider, accounts))
  provider.on('chainChanged', (chainId) => _onChainChanged(provider, chainId))
}

if (typeof window !== 'undefined') {
  window.addEventListener('eip6963:announceProvider', (event) => {
    const detail = event.detail
    if (detail?.info?.rdns && detail?.provider) {
      eip6963Providers.set(detail.info.rdns, detail)
      // Attach change-listeners to every announced provider so account/chain
      // switches in a non-window.ethereum wallet are detected (#921).
      attachWalletListeners(detail.provider)
    }
  })
  // Injected EOAs still expose window.ethereum — bind it too (deduped if it is
  // also announced via EIP-6963, since it's the same provider object).
  if (window.ethereum) attachWalletListeners(window.ethereum)
  // Ask wallets that loaded before this listener was attached to re-announce.
  window.dispatchEvent(new Event('eip6963:requestProvider'))
}

// Known wallets we ship icons + curated names for. Any EIP-6963 wallet not in
// this list still surfaces via discoverEip6963Wallets() with the wallet's own
// self-declared name + icon. rdns lists are intentionally permissive —
// wallets sometimes ship multiple identifiers across versions / variants.
const KNOWN_WALLET_RDNS = {
  metamask: ['io.metamask', 'io.metamask.flask', 'io.metamask.mobile'],
  coinbase: [
    'com.coinbase.wallet',     // legacy extension rdns
    'com.coinbase.smartwallet', // Smart Wallet (popup auth)
    'com.coinbase.cbwallet',    // older variant
    'org.coinbase.wallet',      // some forks
    'com.coinbase',             // shortened
  ],
}

// Heuristic name-based fallback for wallets whose rdns drifts between versions.
// EIP-6963's `info.name` is human-readable but reasonably stable; case-insensitive
// substring match catches "Coinbase Wallet", "Coinbase Smart Wallet", etc.
const KNOWN_WALLET_NAME_PATTERNS = {
  metamask: /\bmetamask\b/i,
  coinbase: /\bcoinbase\b/i,
}

function findEip6963Provider(rdnsList, namePattern = null) {
  // 1. Exact rdns match (preferred — most specific).
  for (const rdns of rdnsList) {
    const entry = eip6963Providers.get(rdns)
    if (entry) return entry.provider
  }
  // 2. Name-based fallback for rdns drift. Last resort because it's fuzzy.
  if (namePattern) {
    for (const entry of eip6963Providers.values()) {
      if (entry.info?.name && namePattern.test(entry.info.name)) {
        return entry.provider
      }
    }
  }
  return null
}

export const WALLET_PROVIDERS = [
  {
    id: 'metamask',
    name: 'MetaMask',
    icon: 'i-token-branded-metamask',
    detect: () => {
      // EIP-6963 first (modern MetaMask) — try known rdns, then fall back
      // to a name-based match in case the rdns drifted between versions.
      const announced = findEip6963Provider(
        KNOWN_WALLET_RDNS.metamask,
        KNOWN_WALLET_NAME_PATTERNS.metamask,
      )
      if (announced) return announced
      // Legacy: window.ethereum.isMetaMask
      if (!window.ethereum) return null
      if (window.ethereum.isMetaMask) return window.ethereum
      // Multi-provider legacy
      if (window.ethereum.providers?.find(p => p.isMetaMask)) {
        return window.ethereum.providers.find(p => p.isMetaMask)
      }
      return null
    },
  },
  {
    id: 'coinbase',
    name: 'Coinbase Wallet',
    icon: 'i-simple-icons-coinbase',
    detect: () => {
      // EIP-6963 first (modern Coinbase Wallet extension). Coinbase has
      // shipped multiple rdns variants — try the known list, then fall
      // back to matching `info.name` for any future variant we haven't
      // hardcoded.
      const announced = findEip6963Provider(
        KNOWN_WALLET_RDNS.coinbase,
        KNOWN_WALLET_NAME_PATTERNS.coinbase,
      )
      if (announced) return announced
      // Legacy patterns (older versions)
      if (window.ethereum?.isCoinbaseWallet) return window.ethereum
      if (window.coinbaseWalletExtension) return window.coinbaseWalletExtension
      if (window.ethereum?.providers?.find(p => p.isCoinbaseWallet)) {
        return window.ethereum.providers.find(p => p.isCoinbaseWallet)
      }
      return null
    },
  },
  {
    id: 'browser',
    name: 'Browser Wallet',
    icon: 'i-lucide-globe',
    detect: () => {
      // Fallback: any window.ethereum provider not already covered.
      return window.ethereum || null
    },
  },
]

// Returns EIP-6963 wallets that aren't in our curated WALLET_PROVIDERS list —
// e.g. Rabby, Brave, Phantom EVM. Each entry shape matches WALLET_PROVIDERS so
// the modal can render them with the wallet's self-declared name + icon.
// Excludes wallets matched by the curated rdns list AND by the curated name
// patterns — otherwise a Coinbase variant with a novel rdns would show up
// twice (once curated via the name-pattern fallback, once dynamic here).
export function discoverEip6963Wallets() {
  const knownRdns = new Set(Object.values(KNOWN_WALLET_RDNS).flat())
  const namePatterns = Object.values(KNOWN_WALLET_NAME_PATTERNS)
  const matchesKnownName = (name) =>
    typeof name === 'string' && namePatterns.some(p => p.test(name))
  const wallets = []
  for (const [rdns, entry] of eip6963Providers) {
    if (knownRdns.has(rdns)) continue
    if (matchesKnownName(entry.info?.name)) continue
    wallets.push({
      id: `eip6963:${rdns}`,
      name: entry.info.name || rdns,
      // Wallet self-declared base64 data URI (per EIP-6963); render directly
      // in <img src=...>. We pass `iconDataUri` instead of `icon` so the
      // modal can branch on which to render.
      iconDataUri: entry.info.icon || null,
      icon: 'i-lucide-wallet',
      detect: () => entry.provider,
    })
  }
  return wallets
}

const STORAGE_KEY = 'archimedes_wallet'
// User-chosen wallet display names keyed by LOWERCASE address, e.g.
// { "0xabc…": "Trading" }. Deliberately a SEPARATE localStorage key from
// STORAGE_KEY: disconnecting clears the connection meta (clearWalletMeta), and
// the names must survive that so signing back into the same passkey wallet
// re-surfaces its name. Clients that never wrote this key just read null —
// same for the legacy {providerId, address} meta shape, which is unchanged.
const WALLET_NAMES_KEY = 'archimedes_wallet_names'

// Synthetic provider id for the Circle Modular Wallets path. Distinct from
// the EOA paths (metamask / coinbase / eip6963:*) so connectWallet() +
// reconnectWallet() can branch cleanly. The MSCA path has no EIP-1193
// provider and no viem WalletClient — txs go through bundler.sendUserOperation
// (Phase 2.5 follow-up); for this PR we surface the MSCA address only.
export const CIRCLE_PROVIDER_ID = 'circle-passkey'

let _walletClient = null
let _provider = null
let _address = null
let _providerId = null
let _smartAccount = null      // populated for the Circle path; null for EOA paths
let _smartAccountClient = null // Circle modular-transport viem client (for bundler)

export function getConnectedProvider() { return _providerId }
export function getAddress() { return _address }
// Returns the Circle smart account when connected via passkey, else null.
// Phase 2.5 uses this to wrap deposit calls in sendUserOperation.
export function getSmartAccount() { return _smartAccount }
// Returns the modular-transport public client paired with the smart
// account — required for createBundlerClient. Null for EOA paths.
export function getSmartAccountClient() { return _smartAccountClient }

function loadWalletNames() {
  try {
    const raw = localStorage.getItem(WALLET_NAMES_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch { return {} }
}

// Returns the user-chosen display name for a wallet address (saved when the
// wallet was created via the Circle passkey register flow), or null when none
// is stored. Middle rung of the UI display-name fallback chain:
//   backend profile display_name → stored wallet name → truncated address.
export function getStoredWalletName(address) {
  if (!address) return null
  const name = loadWalletNames()[address.toLowerCase()]
  return typeof name === 'string' && name.length > 0 ? name : null
}

// Persist connection meta. `name` is optional — the Circle passkey register
// flow passes the user-chosen wallet name; when present it is stored keyed by
// lowercase address under WALLET_NAMES_KEY. The {providerId, address} shape
// under STORAGE_KEY is unchanged so previously stored meta keeps parsing.
function saveWalletMeta(providerId, address, name) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ providerId, address }))
    if (address && typeof name === 'string' && name.trim().length > 0) {
      const names = loadWalletNames()
      names[address.toLowerCase()] = name.trim()
      localStorage.setItem(WALLET_NAMES_KEY, JSON.stringify(names))
    }
  } catch { /* storage unavailable */ }
}

function clearWalletMeta() {
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* */ }
}

function loadWalletMeta() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

// Try to reconnect to a previously connected wallet on page load.
// Uses eth_accounts (non-popup) for EOA wallets to check if the user is
// still authorised. For Circle passkey wallets we DO NOT auto-trigger
// a WebAuthn prompt on page load (would spam users); we restore the
// address from localStorage only, and the smart-account object is
// lazily re-hydrated on the first tx via a fresh login flow.
export async function reconnectWallet() {
  const meta = loadWalletMeta()
  if (!meta) return null

  // Circle passkey path: rebuild the smart account from the stored
  // credential without triggering a WebAuthn prompt. The credential
  // only holds the public key (private key stays in the device's
  // secure enclave), so we can derive the address + signer wrapper
  // silently. Prompt only happens when the user actually signs a
  // user operation later.
  if (meta.providerId === CIRCLE_PROVIDER_ID) {
    if (!circlePasskeyEnabled()) { clearWalletMeta(); return null }
    try {
      const restored = await rehydrateSmartAccount()
      if (!restored) { clearWalletMeta(); return null }
      _address = restored.address
      _providerId = CIRCLE_PROVIDER_ID
      _provider = null
      _walletClient = null
      _smartAccount = restored.smartAccount
      _smartAccountClient = restored.client
      saveWalletMeta(CIRCLE_PROVIDER_ID, _address)
      return { address: _address, provider: _providerId }
    } catch {
      // If rehydration fails (corrupted credential, SDK error, etc.)
      // fall back gracefully — user can re-connect manually.
      clearWalletMeta()
      return null
    }
  }

  const provider = findWalletProvider(meta.providerId)
  if (!provider) { clearWalletMeta(); return null }

  const ethereum = provider.detect()
  if (!ethereum) { clearWalletMeta(); return null }

  try {
    const accounts = await ethereum.request({ method: 'eth_accounts' })
    if (!accounts?.length) { clearWalletMeta(); return null }

    const addr = accounts[0]
    await ensureArcChain(ethereum)

    _provider = ethereum
    _address = addr
    _providerId = meta.providerId
    _walletClient = createWalletClient({
      account: _address,
      chain: arcTestnet,
      transport: custom(ethereum),
    })

    saveWalletMeta(_providerId, _address)
    return { address: _address, provider: _providerId }
  } catch {
    clearWalletMeta()
    return null
  }
}

const ARC_CHAIN_HEX = '0x4cef52'  // 5042002

// MetaMask returns -32002 when a wallet_requestPermissions / eth_requestAccounts
// is already pending — usually because the user dismissed the popup without
// confirming, leaving the request live. Turn this into an actionable message
// instead of bubbling the raw RPC error.
function isAlreadyPendingError(err) {
  return err?.code === -32002
}

async function ensureArcChain(ethereum) {
  // Skip the switch popup if we're already on Arc.
  try {
    const current = await ethereum.request({ method: 'eth_chainId' })
    if (current?.toLowerCase() === ARC_CHAIN_HEX) return
  } catch { /* fall through to switch */ }

  try {
    await ethereum.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: ARC_CHAIN_HEX }],
    })
  } catch (switchError) {
    if (switchError.code === 4902) {
      await ethereum.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: ARC_CHAIN_HEX,
          chainName: 'Arc Testnet',
          nativeCurrency: { name: 'USD Coin', symbol: 'USDC', decimals: 18 },
          rpcUrls: ['https://rpc.testnet.arc.network'],
          blockExplorerUrls: [],
        }],
      })
    } else if (isAlreadyPendingError(switchError)) {
      throw new Error(
        'A wallet request is already open — check your MetaMask extension popup, then try again.',
        { cause: switchError },
      )
    } else {
      throw switchError
    }
  }
}

// Resolve any provider id (curated WALLET_PROVIDERS *or* a dynamic
// `eip6963:<rdns>` id surfaced via discoverEip6963Wallets()).
function findWalletProvider(providerId) {
  const curated = WALLET_PROVIDERS.find(p => p.id === providerId)
  if (curated) return curated
  if (providerId?.startsWith('eip6963:')) {
    return discoverEip6963Wallets().find(p => p.id === providerId) || null
  }
  return null
}

// Connect via Circle Modular Wallets passkey. Returns the same shape as
// connectWallet() so the WalletConnect onConnect callback works
// uniformly. Triggers a WebAuthn prompt (biometric / hardware key) for
// the user — caller should debounce + show a "Authenticating..." state.
export async function connectCircleWallet({ mode = 'login', walletName } = {}) {
  if (!circlePasskeyEnabled()) {
    throw new Error('Circle passkey wallet is not configured.')
  }
  const result = await connectCirclePasskey({ mode, walletName })
  _address = result.address
  _providerId = CIRCLE_PROVIDER_ID
  _provider = null
  _walletClient = null
  _smartAccount = result.smartAccount
  _smartAccountClient = result.client
  // result.walletName is only set on register (trimmed, ≤40 chars) — login is
  // discoverable so no name comes back; any previously stored name for this
  // address is left intact for getStoredWalletName().
  saveWalletMeta(CIRCLE_PROVIDER_ID, _address, result.walletName)
  return { address: _address, provider: CIRCLE_PROVIDER_ID }
}

export async function connectWallet(providerId, opts) {
  if (providerId === CIRCLE_PROVIDER_ID) return connectCircleWallet(opts)

  const provider = findWalletProvider(providerId)
  if (!provider) throw new Error(`Unknown provider: ${providerId}`)

  const ethereum = provider.detect()
  if (!ethereum) throw new Error(`${provider.name} not detected. Please install the extension.`)

  let accounts
  try {
    accounts = await ethereum.request({ method: 'eth_requestAccounts' })
  } catch (err) {
    if (isAlreadyPendingError(err)) {
      throw new Error(
        'A wallet request is already open — check your MetaMask extension popup, then try again.',
        { cause: err },
      )
    }
    if (err?.code === 4001) {
      throw new Error(
        'Connection rejected — approve the request in MetaMask to continue.',
        { cause: err },
      )
    }
    throw err
  }
  if (!accounts?.length) throw new Error('No accounts returned from wallet.')

  await ensureArcChain(ethereum)

  _provider = ethereum
  _address = accounts[0]
  _providerId = providerId
  _walletClient = createWalletClient({
    account: _address,
    chain: arcTestnet,
    transport: custom(ethereum),
  })

  saveWalletMeta(providerId, _address)
  return { address: _address, provider: providerId }
}

export function disconnectWallet() {
  // If we were connected via passkey, also clear the stored P256
  // credential so the next connect starts a fresh register flow.
  if (_providerId === CIRCLE_PROVIDER_ID) clearCircleSession()
  _walletClient = null
  _provider = null
  _address = null
  _providerId = null
  _smartAccount = null
  _smartAccountClient = null
  clearWalletMeta()
}

export async function getWalletClient() {
  if (_walletClient) return _walletClient
  if (_providerId === CIRCLE_PROVIDER_ID) {
    // Passkey wallets sign via Circle's bundler (executeUserOp), not viem
    // writeContract — callers should branch on getConnectedProvider() and
    // use the executor for that path (message signing: signSiweMessage below).
    // This error fires only if a code path forgot to branch.
    throw new Error(
      'This action is not yet wired for passkey wallets. ' +
      'The deposit flow uses Circle bundler execution; other flows still need that wrapper.',
    )
  }
  throw new Error('No wallet connected. Click "Connect Wallet" to continue.')
}

// Sign a plain text message with WHATEVER wallet is connected (#869).
// EOA path: viem walletClient.signMessage (secp256k1 personal_sign).
// Circle passkey path: the smart account's WebAuthn owner signs the account's
// replay-safe hash (ERC-1271 convention). Circle's `toCircleSmartAccount` is a
// viem `toSmartAccount`, whose `signMessage` ALREADY returns the correct wire
// format for the backend's deployless verifier: a bare ERC-1271 signature once
// the account is deployed, and an ERC-6492-wrapped signature (with the account's
// own factory args) while it is still counterfactual. We must NOT wrap it a
// second time — an earlier version did (#870/#871) and that double ERC-6492
// wrap is what made every passkey SIWE fail with "validator ran, rejected":
// the outer wrapper deployed the wrong CREATE2 address so `isValidSignature`
// landed on an empty account. Verified against a captured live signature: the
// singly-wrapped output of `signMessage` verifies true via viem's own
// `verifyMessage` (== our backend); re-wrapping it verifies false.
export async function signSiweMessage(message) {
  if (_providerId === CIRCLE_PROVIDER_ID && _smartAccount) {
    return _smartAccount.signMessage({ message })
  }
  const walletClient = await getWalletClient()
  return walletClient.signMessage({ message })
}

// Returns all wallet providers detected in the page — curated WALLET_PROVIDERS
// that pass their detect(), plus any EIP-6963 wallet the dApp doesn't have a
// curated entry for (Rabby, Brave, Phantom EVM, etc.). The Circle passkey
// option is included whenever VITE_CIRCLE_CLIENT_KEY is set — it requires no
// extension, just WebAuthn support, so it shows up in every browser.
export function getAvailableProviders() {
  const curated = WALLET_PROVIDERS.filter(p => p.detect() !== null)
  const discovered = discoverEip6963Wallets()
  // Drop the generic 'browser' fallback if a real EIP-6963 wallet is present —
  // the generic option exists for users who only have window.ethereum injected
  // without identifying itself, which is exactly what EIP-6963 fixes.
  const hasReal = curated.some(p => p.id !== 'browser') || discovered.length > 0
  const filtered = hasReal ? curated.filter(p => p.id !== 'browser') : curated
  const passkey = circlePasskeyEnabled()
    ? [{
        id: CIRCLE_PROVIDER_ID,
        name: 'Sign in with Passkey',
        icon: 'i-lucide-fingerprint',
        // Synthetic provider — no EIP-1193 detect; presence is implied by
        // circlePasskeyEnabled() being true.
        detect: () => true,
      }]
    : []
  return [...passkey, ...filtered, ...discovered]
}

// Account/chain-change listeners are attached at module load via
// attachWalletListeners() — bound to window.ethereum AND every EIP-6963
// announced provider (see the top of this file, #921).

// ─── ABIs (minimal, just what we need) ──────────────────────

export const ORACLE_ABI = [
  { name: 'price',       type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'symbol',      type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'string'  }] },
  { name: 'lastUpdated', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'isFresh',     type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'bool'    }] },
  { name: 'setPrice',    type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256', name: '_newPrice' }], outputs: [] },
]

export const TOKEN_ABI = [
  { name: 'totalSupply',   type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'balanceOf',     type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { name: 'approve',       type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address' }, { type: 'uint256' }], outputs: [{ type: 'bool' }] },
  { name: 'allowance',     type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { name: 'symbol',        type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'string' }] },
  { name: 'decimals',      type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint8' }] },
]

// Minimal ABI for USDC approve/allowance — used by DepositFlow stepper.
// Same as TOKEN_ABI but scoped to the two functions needed for the deposit flow.
export const USDC_ABI = [
  { name: 'approve',       type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address', name: 'spender' }, { type: 'uint256', name: 'amount' }], outputs: [{ type: 'bool' }] },
  { name: 'allowance',     type: 'function', stateMutability: 'view', inputs: [{ type: 'address', name: 'owner' }, { type: 'address', name: 'spender' }], outputs: [{ type: 'uint256' }] },
]

export const SYNTH_VAULT_ABI = [
  { name: 'mint',                type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256', name: 'amountUsdc' }], outputs: [{ type: 'uint256' }] },
  { name: 'burn',                type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256', name: 'synthAmount' }], outputs: [{ type: 'uint256' }] },
  { name: 'previewMint',         type: 'function', stateMutability: 'view', inputs: [{ type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { name: 'previewBurn',         type: 'function', stateMutability: 'view', inputs: [{ type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { name: 'totalCollateral',     type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'vaultCollateralization', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
]

export const AMM_ROUTER_ABI = [
  { name: 'createPool',    type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address' }, { type: 'address' }], outputs: [{ type: 'address' }] },
  { name: 'getPool',       type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }, { type: 'address' }], outputs: [{ type: 'address' }] },
  { name: 'getAllPools',   type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address[]' }] },
  { name: 'swap',          type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address' }, { type: 'address' }, { type: 'uint256' }, { type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { name: 'getAmountOut',  type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }, { type: 'address' }, { type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { name: 'addLiquidity',  type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address' }, { type: 'address' }, { type: 'uint256' }, { type: 'uint256' }, { type: 'uint256' }], outputs: [{ type: 'uint256' }] },
]

export const TRACE_REGISTRY_ABI = [
  { name: 'publishTrace',   type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address' }, { type: 'bytes32' }, { type: 'bytes' }], outputs: [{ type: 'uint256' }] },
  { name: 'verifyTrace',    type: 'function', stateMutability: 'view', inputs: [{ type: 'uint256' }, { type: 'bytes' }], outputs: [{ type: 'bool' }] },
  { name: 'traceCount',     type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'getTracesByVault', type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256[]' }] },
  { name: 'getTraceById',   type: 'function', stateMutability: 'view', inputs: [{ type: 'uint256' }], outputs: [{ type: 'address', name: 'agent' }, { type: 'address', name: 'vault' }, { type: 'bytes32', name: 'traceHash' }, { type: 'uint256', name: 'timestamp' }, { type: 'bytes', name: 'metadata' }] },
]

export const ASSET_REGISTRY_ABI = [
  { name: 'getAllSynthetics', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address[]' }] },
  { name: 'vaultCount',       type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'getLeaderboard',   type: 'function', stateMutability: 'view', inputs: [{ type: 'uint8' }, { type: 'uint256' }, { type: 'uint256' }], outputs: [{ type: 'address[]' }] }, // (tier, offset, limit) — paginated (#927)
]

export const VAULT_ABI = [
  { name: 'deposit',             type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { name: 'withdraw',            type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256' }, { type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  // ERC-4626 share-based redemption (Issue #466 — non-custodial withdraw).
  // redeem(shares, receiver, owner) burns `shares` from `owner` and sends the
  // resulting USDC to `receiver`; previewRedeem(shares) quotes the USDC out
  // off-chain so the user can confirm before signing. Both exist on the deployed
  // Vault.json ABI (verified Issue #466) — this just surfaces them to the UI.
  { name: 'redeem',              type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256' }, { type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { name: 'previewRedeem',       type: 'function', stateMutability: 'view', inputs: [{ type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { name: 'totalAssets',         type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'totalSupply',         type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'balanceOf',           type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { name: 'getHoldings',         type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address[]' }, { type: 'uint256[]' }] },
  { name: 'creator',             type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address' }] },
  { name: 'tier',                type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint8' }] },
  { name: 'paused',              type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'bool' }] },
  { name: 'highWaterMark',       type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'asset',               type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address' }] },
  { name: 'approve',             type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address' }, { type: 'uint256' }], outputs: [{ type: 'bool' }] },
  { name: 'name',                type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'string' }] },
  { name: 'symbol',              type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'string' }] },
  { name: 'managementFeeBps',    type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint16' }] },
  { name: 'performanceFeeBps',   type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint16' }] },
  { name: 'setTargetAllocations', type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address[]', name: 'tokens' }, { type: 'uint256[]', name: 'weightsBps' }], outputs: [] },
  { name: 'getTargetAllocations', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address[]' }, { type: 'uint256[]' }] },
  { name: 'setTokenOracles', type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address[]', name: 'tokens' }, { type: 'address[]', name: 'oracles' }], outputs: [] },
  { name: 'tokenOracle', type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }], outputs: [{ type: 'address' }] },
  { name: 'setAgent', type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address', name: '_agent' }], outputs: [] },
  { name: 'agent', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address' }] },
  { name: 'isAgentAssisted', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'bool' }] },
]

export const VAULT_FACTORY_ABI = [
  { name: 'createVault',    type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'string' }, { type: 'string' }, { type: 'uint16' }, { type: 'uint16' }, { type: 'bool' }], outputs: [{ type: 'address' }] },
  { name: 'getVaults',      type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address[]' }] },
  { name: 'vaultCount',     type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'agentAddress',   type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'address' }] },
  { name: 'getVaultsByCreator', type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }], outputs: [{ type: 'address[]' }] },
  // VaultCreated event — used to extract new vault address from receipt
  { name: 'VaultCreated',   type: 'event', inputs: [
    { name: 'vault',   type: 'address', indexed: true },
    { name: 'creator', type: 'address', indexed: true },
    { name: 'name',    type: 'string',  indexed: false },
    { name: 'symbol',  type: 'string',  indexed: false },
    { name: 'tier',    type: 'uint8',   indexed: false },
  ]},
]

// ─── Deployed addresses from .env ────────────────────────────

export const USDC = "0x3600000000000000000000000000000000000000"

// USDC on Arc testnet uses 6 decimals (verified via
// `eth_call decimals() at 0x3600...`). Used by getUsdcBalance() below.
export const USDC_DECIMALS = 6

// Read the USDC balance for an arbitrary address (the wallet menu uses
// this to show the user how many test USDC they have). Returns a Number
// in USDC units (already divided by 10^USDC_DECIMALS); returns null on
// any failure so the caller can fall back to a placeholder rather than
// crash the menu render.
export async function getUsdcBalance(address) {
  if (!address) return null
  try {
    const raw = await publicClient.readContract({
      address: USDC,
      abi: [
        { name: 'balanceOf', type: 'function', stateMutability: 'view',
          inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
      ],
      functionName: 'balanceOf',
      args: [address],
    })
    return Number(raw) / 10 ** USDC_DECIMALS
  } catch {
    return null
  }
}

/** Read the raw 6-dec USDC balance for an arbitrary address (bigint). */
export async function usdcBalanceOfRaw(address) {
  return await publicClient.readContract({
    address: USDC,
    abi: [
      { name: 'balanceOf', type: 'function', stateMutability: 'view',
        inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
    ],
    functionName: 'balanceOf',
    args: [address],
  })
}

/** Minimum idle USDC (raw 6-dec) a vault must hold before a strategy may be
 *  published. Mirrors MARKETPLACE_MIN_VAULT_FUNDS_RAW on the backend.
 *  1000000 = 1 USDC. Backend-authoritative — this is a client pre-check only. */
export const MIN_VAULT_FUNDS_RAW = BigInt(import.meta.env.VITE_MARKETPLACE_MIN_VAULT_FUNDS_RAW ?? '1000000')

export const ASSETS = [
  { id: 'TSLA',   name: 'Tesla',      sym: 'sTSLA',   icon: 'i-simple-icons-tesla',          oracle: '0xe1c9f2b11be97097223a66a188fca541e07873a6', vault: '0xf0356600e26c6c403ec4f5b36b0e3380bb0609ab', token: '0xd514cd27baf762c650536765cde9b61c876abacd' },
  { id: 'NVDA',   name: 'Nvidia',     sym: 'sNVDA',   icon: 'i-simple-icons-nvidia',          oracle: '0xeb36acf88e739dd312de8278985262146a017374', vault: '0x4c3cdc2bf44195ad8a4d201c8afbd453949a8781', token: '0x805e75019a1291a598dfc134ad2519121a35fb11' },
  { id: 'SPY',    name: 'S&P 500',    sym: 'sSPY',    icon: 'i-lucide-trending-up',           oracle: '0xd8161a8eeab7c7100e2863abe3d5f346b5ff9e52', vault: '0xd8d7855f76c384638cf1dfc3575ecff3538764b4', token: '0x6fea38dedea0c6bb66ce93e5383c34385d8b889f' },
  { id: 'BTC',    name: 'Bitcoin',    sym: 'sBTC',    icon: 'i-cryptocurrency-color-btc',     oracle: '0x6cc5f621c4e3b46152e69e5c9873689cbb4a85e8', vault: '0x92990ed6f5c8cd72752ca9aeafad422269225c43', token: '0x317e82be8f7cba6c162ab968fcf695d88e8e0359' },
  { id: 'GOLD',   name: 'Gold ETF',   sym: 'sGOLD',   icon: 'i-lucide-coins',                 oracle: '0x35fccde01ae8728c7a7cb83c3f59c701ebecc633', vault: '0x124b5c5da57d209b28d4997aaf6d4e96711efd5a', token: '0xf384562c8bdafce52400eb6839f195695f6fa276' },
  { id: 'OIL',    name: 'Oil ETF',    sym: 'sOIL',    icon: 'i-lucide-fuel',                  oracle: '0x79f354524fd09af16d841a2221af2b2b7bc432c8', vault: '0xfa942399e36959c8060c3a82a610d680a7ac6d22', token: '0x46cead4120f17a968ba1168f1a56563962cf3c4b' },
  { id: 'NIKKEI', name: 'Nikkei ETF', sym: 'sNKY',    icon: 'i-lucide-bar-chart-2',           oracle: '0xcd34a4103ad64a3cf729b1b1a58295ccc957fcee', vault: '0xb26029ca37c09400ca921f00fc541cd42143b508', token: '0x445b8f0f827a0d384d1b8ccf18cbc6ec8a543376' },
]

// New contract addresses — set these after deploying via deploy-new.mjs
export const NEW_CONTRACTS = {
  ammRouter:       '0x090f8E245F2831b81c9ff21661FBd0cb1383f82D',
  vaultFactory:    '0x32A3e0D0a8215D77e3B92fa6d9b4Dbe19f255671',
  traceRegistry:   '0x44bD55c0DdF757e584a41fb7F3B6a47b4C5982ba',
  assetRegistry:   '0x79fc95A10E8240116006084439B650BA9e72F3cA',
  // paymentSplitter: source is contracts/src/PaymentSplitter.sol (deployed at T3.2).
  paymentSplitter:     '0x0000000000000000000000000000000000000000',
}
