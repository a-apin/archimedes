import { createPublicClient, createWalletClient, custom, http } from 'viem'

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

export const WALLET_PROVIDERS = [
  {
    id: 'metamask',
    name: 'MetaMask',
    icon: '🦊',
    detect: () => {
      if (!window.ethereum) return null
      // MetaMask injects window.ethereum with isMetaMask
      if (window.ethereum.isMetaMask) return window.ethereum
      // Some browsers have multiple wallets — check for MetaMask specifically
      if (window.ethereum.providers?.find(p => p.isMetaMask)) {
        return window.ethereum.providers.find(p => p.isMetaMask)
      }
      return null
    },
  },
  {
    id: 'coinbase',
    name: 'Coinbase Wallet',
    icon: '🔵',
    detect: () => {
      // Coinbase Wallet extension
      if (window.ethereum?.isCoinbaseWallet) return window.ethereum
      // Coinbase injected as separate provider
      if (window.coinbaseWalletExtension) return window.coinbaseWalletExtension
      // Multiple providers — find Coinbase
      if (window.ethereum?.providers?.find(p => p.isCoinbaseWallet)) {
        return window.ethereum.providers.find(p => p.isCoinbaseWallet)
      }
      return null
    },
  },
  {
    id: 'browser',
    name: 'Browser Wallet',
    icon: '🌐',
    detect: () => {
      // Fallback: any window.ethereum provider
      return window.ethereum || null
    },
  },
]

const STORAGE_KEY = 'archimedes_wallet'

let _walletClient = null
let _provider = null
let _address = null
let _providerId = null

export function getConnectedProvider() { return _providerId }
export function getAddress() { return _address }

function saveWalletMeta(providerId, address) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ providerId, address }))
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
// Uses eth_accounts (non-popup) to check if the user is still authorised.
export async function reconnectWallet() {
  const meta = loadWalletMeta()
  if (!meta) return null

  const provider = WALLET_PROVIDERS.find(p => p.id === meta.providerId)
  if (!provider) { clearWalletMeta(); return null }

  const ethereum = provider.detect()
  if (!ethereum) { clearWalletMeta(); return null }

  try {
    const accounts = await ethereum.request({ method: 'eth_accounts' })
    if (!accounts?.length) { clearWalletMeta(); return null }

    const addr = accounts[0]

    // Ensure Arc Testnet is selected
    const chainIdHex = '0x4cef52'
    try {
      await ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: chainIdHex }],
      })
    } catch (switchError) {
      if (switchError.code === 4902) {
        await ethereum.request({
          method: 'wallet_addEthereumChain',
          params: [{
            chainId: chainIdHex,
            chainName: 'Arc Testnet',
            nativeCurrency: { name: 'USD Coin', symbol: 'USDC', decimals: 18 },
            rpcUrls: ['https://rpc.testnet.arc.network'],
            blockExplorerUrls: [],
          }],
        })
      } else {
        throw switchError
      }
    }

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

export async function connectWallet(providerId) {
  const provider = WALLET_PROVIDERS.find(p => p.id === providerId)
  if (!provider) throw new Error(`Unknown provider: ${providerId}`)

  const ethereum = provider.detect()
  if (!ethereum) throw new Error(`${provider.name} not detected. Please install the extension.`)

  // Request accounts
  const accounts = await ethereum.request({ method: 'eth_requestAccounts' })
  if (!accounts?.length) throw new Error('No accounts returned')

  // Switch to Arc Testnet (chain ID from RPC: 0x4cef52 = 5042002)
  const chainIdHex = '0x4cef52'
  try {
    await ethereum.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: chainIdHex }],
    })
  } catch (switchError) {
    // Chain not added — add it
    if (switchError.code === 4902) {
      await ethereum.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: chainIdHex,
          chainName: 'Arc Testnet',
          nativeCurrency: { name: 'USD Coin', symbol: 'USDC', decimals: 18 },
          rpcUrls: ['https://rpc.testnet.arc.network'],
          blockExplorerUrls: [],
        }],
      })
    } else {
      throw switchError
    }
  }

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
  _walletClient = null
  _provider = null
  _address = null
  _providerId = null
  clearWalletMeta()
}

export async function getWalletClient() {
  if (_walletClient) return _walletClient
  throw new Error('No wallet connected. Click "Connect Wallet" to continue.')
}

// Check which providers are available
export function getAvailableProviders() {
  return WALLET_PROVIDERS.filter(p => p.detect() !== null)
}

// Listen for account/chain changes from the wallet extension
if (typeof window !== 'undefined' && window.ethereum) {
  window.ethereum.on?.('accountsChanged', (accounts) => {
    if (!accounts?.length) {
      disconnectWallet()
      window.dispatchEvent(new CustomEvent('wallet-changed', { detail: { address: null } }))
    } else {
      _address = accounts[0]
      if (_providerId) saveWalletMeta(_providerId, _address)
      if (_provider) {
        _walletClient = createWalletClient({
          account: _address,
          chain: arcTestnet,
          transport: custom(_provider),
        })
      }
      window.dispatchEvent(new CustomEvent('wallet-changed', { detail: { address: _address } }))
    }
  })
  window.ethereum.on?.('chainChanged', () => {
    window.location.reload()
  })
}

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
  { name: 'getLeaderboard',   type: 'function', stateMutability: 'view', inputs: [{ type: 'uint8' }, { type: 'uint256' }], outputs: [{ type: 'address[]' }] },
]

export const VAULT_ABI = [
  { name: 'deposit',             type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { name: 'withdraw',            type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'uint256' }, { type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
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

export const ASSETS = [
  { id: 'TSLA',   name: 'Tesla',      sym: 'sTSLA',   emoji: '🚗', oracle: '0xe1c9f2b11be97097223a66a188fca541e07873a6', vault: '0xf0356600e26c6c403ec4f5b36b0e3380bb0609ab', token: '0xd514cd27baf762c650536765cde9b61c876abacd' },
  { id: 'NVDA',   name: 'Nvidia',     sym: 'sNVDA',   emoji: '🎮', oracle: '0xeb36acf88e739dd312de8278985262146a017374', vault: '0x4c3cdc2bf44195ad8a4d201c8afbd453949a8781', token: '0x805e75019a1291a598dfc134ad2519121a35fb11' },
  { id: 'SPY',    name: 'S&P 500',    sym: 'sSPY',    emoji: '📈', oracle: '0xd8161a8eeab7c7100e2863abe3d5f346b5ff9e52', vault: '0xd8d7855f76c384638cf1dfc3575ecff3538764b4', token: '0x6fea38dedea0c6bb66ce93e5383c34385d8b889f' },
  { id: 'BTC',    name: 'Bitcoin',    sym: 'sBTC',    emoji: '₿',  oracle: '0x6cc5f621c4e3b46152e69e5c9873689cbb4a85e8', vault: '0x92990ed6f5c8cd72752ca9aeafad422269225c43', token: '0x317e82be8f7cba6c162ab968fcf695d88e8e0359' },
  { id: 'GOLD',   name: 'Gold ETF',   sym: 'sGOLD',   emoji: '🥇', oracle: '0x35fccde01ae8728c7a7cb83c3f59c701ebecc633', vault: '0x124b5c5da57d209b28d4997aaf6d4e96711efd5a', token: '0xf384562c8bdafce52400eb6839f195695f6fa276' },
  { id: 'OIL',    name: 'Oil ETF',    sym: 'sOIL',    emoji: '🛢️', oracle: '0x79f354524fd09af16d841a2221af2b2b7bc432c8', vault: '0xfa942399e36959c8060c3a82a610d680a7ac6d22', token: '0x46cead4120f17a968ba1168f1a56563962cf3c4b' },
  { id: 'NIKKEI', name: 'Nikkei ETF', sym: 'sNKY',    emoji: '🗾', oracle: '0xcd34a4103ad64a3cf729b1b1a58295ccc957fcee', vault: '0xb26029ca37c09400ca921f00fc541cd42143b508', token: '0x445b8f0f827a0d384d1b8ccf18cbc6ec8a543376' },
]

// New contract addresses — set these after deploying via deploy-new.mjs
export const NEW_CONTRACTS = {
  ammRouter:       '0xd5b829f9d364a8bbe1caf6c8b19cb05371b178f4',
  vaultFactory:    '0xca873414070844aeb98b0bf1051f81969c79cc32',
  traceRegistry:   '0x42d8a23edb897cbee203e9fa197eb05ab5106ca6',
  assetRegistry:   '0x2d44550711137916df6175587d17886281a0fbc7',
}
