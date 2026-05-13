import { createPublicClient, http } from 'viem'

const arcTestnet = {
  id: 1203948,
  name: 'Arc Testnet',
  nativeCurrency: { name: 'USD Coin', symbol: 'USDC', decimals: 6 },
  rpcUrls: { default: { http: ['https://rpc.testnet.arc.network'] } },
}

export const client = createPublicClient({
  chain: arcTestnet,
  transport: http(),
})

export const ORACLE_ABI = [
  { name: 'price',       type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'symbol',      type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'string'  }] },
  { name: 'lastUpdated', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
  { name: 'isFresh',     type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'bool'    }] },
]

export const VAULT_ABI = [
  { name: 'totalCollateral', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
]

export const TOKEN_ABI = [
  { name: 'totalSupply', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
]

export const ASSETS = [
  { id: 'TSLA',   name: 'Tesla',      sym: 'sTSLA',   emoji: '🚗', oracle: '0x8c77f2920a7d440dc07d824fbe7e39166c5a27a0', vault: '0x0dee733b938fac420e3c7feefc031d620f5430a4', token: '0x18e711913f7aa89556d4146c2b3b0bbc24241e74' },
  { id: 'NVDA',   name: 'Nvidia',     sym: 'sNVDA',   emoji: '🎮', oracle: '0x04e75590f1a37fe05714d9f7d48b2b8ad5c176e8', vault: '0x2ace30d41f35b74b65c31aa9a58439fb7647f757', token: '0x0a0d4719afcadf76a4be72bfcfa1c11d372a8894' },
  { id: 'SPY',    name: 'S&P 500',    sym: 'sSPY',    emoji: '📈', oracle: '0x3c6e67d264b2f1275ddbf1c1354eb0b6d2747c2d', vault: '0x39ad3053c744d85b7bba21c3ef199da9e838e7a2', token: '0xcdf63eeb1a0e96d0c0b372881795dca8d5055d23' },
  { id: 'BTC',    name: 'Bitcoin',    sym: 'sBTC',    emoji: '₿',  oracle: '0xfb0d998fac772b3e06ca655753a885c85a108517', vault: '0x18c1748d48cf4931c4b480f94da1767c36beeb2a', token: '0xca4acd88ef5da78e405d78e6a388e329038816c2' },
  { id: 'GOLD',   name: 'Gold ETF',   sym: 'sGOLD',   emoji: '🥇', oracle: '0x38bbb0f02cf3a7c95fbec8d51dc57d24fc0d541f', vault: '0xe647251ed8996a7f34c7ac84546073a05194a38c', token: '0xf85aab5c6b17957a9b824a7571697a65092eb258' },
  { id: 'OIL',    name: 'Oil ETF',    sym: 'sOIL',    emoji: '🛢️', oracle: '0x134d4dc3329d8b474916697e1440755c052b4dbd', vault: '0xafcaad69a3eaa1c6695b7b101a4c3ee789a68724', token: '0x5d0d1a2f0ba848358577403fddec649475f248e8' },
  { id: 'NIKKEI', name: 'Nikkei ETF', sym: 'sNIKKEI', emoji: '🗾', oracle: '0x2a7e0259174674374759b4c3cd0b5ae74bae5023', vault: '0xaf13034e4a294d21143e76ce4690bb1defd8cd53', token: '0x95d75fdeca8d2806b7371444d85346b8a8295cf7' },
]
