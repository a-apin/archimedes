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
  { id: 'TSLA',   name: 'Tesla',         sym: 'sTSLA',   emoji: '🚗', oracle: '0xc30ca947ec5b4699a51b4ec1fd4216ef54e2e4e6', vault: '0xf26a19ac45dc3ac24229250df7b83c539241d494', token: '0x0d07b221847e41513c878edb51235c52e557e2d4' },
  { id: 'NVDA',   name: 'Nvidia',        sym: 'sNVDA',   emoji: '🎮', oracle: '0x45da8ec9e3ad282ff047229d7595eec9bc0cb691', vault: '0x4dafdabcb6c21da1d9ab84bc6dc49b1d70e2f9b1', token: '0xac98528206458143fae0dcd944af49622ba042be' },
  { id: 'SPY',    name: 'S&P 500',       sym: 'sSPY',    emoji: '📈', oracle: '0xde3dd38ffd13a72aafb299fd540077f73b01ae07', vault: '0x754a504078bd36efdd146f869f957be198ed050e', token: '0x2e9d798d76f531dec31ef6862395dcdd957bb684' },
  { id: 'BTC',    name: 'Bitcoin',       sym: 'sBTC',    emoji: '₿',  oracle: '0xe46110d597de05ccc6a1a90a3e1ed11798366baf', vault: '0xf1d5d5deeea8cda70dac410f441d60b4aadcfaed', token: '0xdbaaf59760b5925a0cd1380ceaf2905863ece520' },
  { id: 'GOLD',   name: 'Gold ETF',      sym: 'sGOLD',   emoji: '🥇', oracle: '0xef7fe5c8b466beb56a2719814cc4e680dc03cbfb', vault: '0x6b7b1b5a866ebfe20befe0bbb9405698c3fbdd86', token: '0x2a87476a4b543419cb3123c428eac764f27f30a4' },
  { id: 'OIL',    name: 'Oil ETF',       sym: 'sOIL',    emoji: '🛢️', oracle: '0x10a1d309ec174e81b1bd4bed4050ff6ee531fa90', vault: '0x7c5a9238a1134ceeb924ff848fab7d9719312442', token: '0x37c232c273e0fc637b6cc1e96f710e670f50a27e' },
  { id: 'NIKKEI', name: 'Nikkei ETF',    sym: 'sNIKKEI', emoji: '🗾', oracle: '0x00c4b9494452c175267817f3a429c47f2e14adf9', vault: '0x1516e7df808c0aa95495fc0ca1aa0ab24d5f36f0', token: '0x69245d872ac80d15beae21276ff34c6220255bcb' },
]
