/**
 * Chain identity for the frontend — one place, env-overridable (#1240).
 *
 * The Arc mainnet cutover puts the payment rail (USDC / x402 / Gateway /
 * PaymentSplitter) on mainnet while vault, synth, AMM and oracle execution
 * stays on testnet. Before this module the chain id was written as the literal
 * 5042002 in seven places plus a separately-written hex copy, so there was no
 * single thing to change and no way to say which of the two chains a call site
 * meant.
 *
 * History worth keeping, because a doc in this repo says otherwise:
 * `ui/src/siwe.js` DID hold `VITE_ARC_CHAIN_ID ?? '5042002'` (added 7415b245,
 * 2026-06-13). The file was deleted whole in 95c9faf7 (2026-07-28) and the seam
 * went with it, leaving the literals behind. `docs/sprint/README.md` records
 * this as a file that "has never existed", which is what a working-tree grep
 * shows — it cannot distinguish a deleted file from a fictional one.
 *
 * Vite inlines `import.meta.env.*` at BUILD time, so these are build constants
 * rather than runtime configuration. A wrong value is a wrong build.
 */

// Arc testnet. The safe default in every direction: it moves no real money, so
// falling back to it can only fail closed.
export const DEFAULT_CHAIN_ID = 5042002
export const DEFAULT_RPC_URL = 'https://rpc.testnet.arc.network'

/**
 * Parse a chain id from an environment string.
 *
 * Returns `fallback` for unset, blank, or malformed input, and reports the
 * malformed case on the console rather than swallowing it. Falling back is the
 * right call ONLY because the fallback is testnet: a build that meant to reach
 * mainnet and instead reaches testnet fails to move money, while the reverse
 * would move it somewhere nobody chose. If the default here ever becomes a
 * mainnet id, this function must throw instead of falling back.
 */
export function resolveChainId(raw, fallback = DEFAULT_CHAIN_ID) {
  if (raw === undefined || raw === null) return fallback
  const text = String(raw).trim()
  if (text === '') return fallback
  // Number() would accept '0x4cef52', ' 12 ', '1e3' and '' — all of which are
  // either the wrong notation for this variable or silently not what was
  // written. Require plain decimal digits.
  if (!/^\d+$/.test(text)) {
    console.error(
      `[chain-config] ignoring malformed chain id ${JSON.stringify(raw)}; ` +
        `expected decimal digits. Falling back to ${fallback}.`,
    )
    return fallback
  }
  const parsed = Number(text)
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    console.error(`[chain-config] chain id ${text} is out of range. Falling back to ${fallback}.`)
    return fallback
  }
  return parsed
}

/**
 * EIP-155 hex form, as `wallet_switchEthereumChain` wants it.
 *
 * Derived rather than written down. The previous code carried '0x4cef52' as its
 * own literal next to the decimal one, so changing the id would have left the
 * switch-chain call pointing at the old network with nothing to catch it.
 */
export function chainIdToHex(id) {
  return `0x${Number(id).toString(16)}`
}

function envValue(key) {
  // `import.meta.env` is undefined outside a Vite build (node --test), which is
  // the same thing as unset.
  return import.meta.env?.[key]
}

/** The chain vaults, synths, AMM and oracles live on — and every contract address. */
export const EXECUTION_CHAIN_ID = resolveChainId(envValue('VITE_ARC_CHAIN_ID'))
export const EXECUTION_CHAIN_HEX = chainIdToHex(EXECUTION_CHAIN_ID)
export const EXECUTION_RPC_URL = envValue('VITE_ARC_RPC_URL') || DEFAULT_RPC_URL

/**
 * The chain USDC settles on. Defaults to the execution chain, so a build that
 * sets nothing behaves exactly as it does now.
 */
export const PAYMENTS_CHAIN_ID = resolveChainId(envValue('VITE_ARC_PAYMENTS_CHAIN_ID'), EXECUTION_CHAIN_ID)

/**
 * True when payments and execution are different chains.
 *
 * The browser wallet flow turns on this: one chain needs no switching, two do.
 * Nothing consumes it yet — the wallet UX for a split is deliberately out of
 * scope here and #1240 argues for launching API-first precisely to avoid it.
 */
export const IS_SPLIT_CHAIN = PAYMENTS_CHAIN_ID !== EXECUTION_CHAIN_ID

/** viem chain object for the execution chain. Shared so the EOA and passkey paths cannot drift. */
export const arcExecutionChain = {
  id: EXECUTION_CHAIN_ID,
  name: 'Arc Testnet',
  nativeCurrency: { name: 'USD Coin', symbol: 'USDC', decimals: 18 },
  rpcUrls: { default: { http: [EXECUTION_RPC_URL] } },
}
