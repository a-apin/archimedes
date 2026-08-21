// Effectful half of the real x402 payment flow — Gateway balance reads, the
// approve+deposit two-transaction funding flow, and EIP-712 signing via the
// connected wallet. Kept OUT of ./generateQuote.js so that module stays a
// pure, DOM/wallet-free state machine unit-testable under node:test (see its
// own header comment) — this file is the wallet/chain-touching complement,
// mirroring the DepositFlow.jsx / circle-tx-executor.js split already
// established for vault deposits.
//
// Full wallet matrix (#1298, Dan's 2026-08-21 directive): EOA/injected
// wallets (MetaMask, Coinbase, Brave, Phantom — anything EIP-6963) sign via
// the viem WalletClient; the Circle passkey smart account signs typed data
// through its viem SmartAccount (ERC-1271/6492) and transacts through the
// bundler executor. paymentWalletKind() below is the branch point, and
// 'none' means the session has no connected wallet yet — the UI must offer
// the CONNECT flow there, never a dead-end message.
//
// No new dependency: viem is already a dependency (see config.js), and the
// two ABIs below follow this repo's established "minimal inline ABI, just
// what's needed" convention (config.js's USDC_ABI / VAULT_ABI).

import { parseUnits } from "viem";
import { encodeCall, executeUserOp } from "./circle-tx-executor";
import {
	CIRCLE_PROVIDER_ID,
	USDC_DECIMALS,
	ensureArcChain,
	getConnectedProvider,
	getRawProvider,
	getSmartAccount,
	getSmartAccountClient,
	getWalletClient,
	publicClient,
} from "./config";
import { buildPaymentSignatureHeader, buildTransferAuthorizationTypedData } from "./generateQuote";

// Circle Gateway Wallet contract — just the two functions the balance-check
// and deposit flow need. The contract address itself is NOT hardcoded here:
// it comes from the 402's own `requirements.extra.verifyingContract` (the
// same value the EIP-712 domain signs against), so this stays correct if the
// backend ever points at a different Gateway deployment.
const GATEWAY_ABI = [
	{
		name: "availableBalance",
		type: "function",
		stateMutability: "view",
		inputs: [
			{ type: "address", name: "token" },
			{ type: "address", name: "depositor" },
		],
		outputs: [{ type: "uint256" }],
	},
	{
		name: "deposit",
		type: "function",
		stateMutability: "nonpayable",
		inputs: [
			{ type: "address", name: "token" },
			{ type: "uint256", name: "value" },
		],
		outputs: [],
	},
];

// Minimal USDC ABI for the approve leg of the deposit flow — scoped to just
// `approve`, matching config.js's USDC_ABI shape (which also has `allowance`,
// unused here).
const USDC_APPROVE_ABI = [
	{
		name: "approve",
		type: "function",
		stateMutability: "nonpayable",
		inputs: [
			{ type: "address", name: "spender" },
			{ type: "uint256", name: "amount" },
		],
		outputs: [{ type: "bool" }],
	},
];

/**
 * Which kind of payment-capable wallet is connected right now (#1298 wallet
 * matrix): 'eoa' — any injected/EIP-6963 wallet (MetaMask, Coinbase, Brave,
 * Phantom, …) with a viem WalletClient; 'circle' — the Circle passkey smart
 * account, which signs typed data through its viem SmartAccount and
 * transacts through the bundler executor; 'none' — nothing connected in this
 * session. Generate.jsx branches on this to offer the CONNECT flow for
 * 'none' (the prior build dead-ended there: it told MetaMask users to "use a
 * browser wallet" while giving them no connect button — the exact failure
 * Dan hit in all three browsers).
 */
export function paymentWalletKind() {
	const provider = getConnectedProvider();
	if (!provider) return "none";
	return provider === CIRCLE_PROVIDER_ID ? "circle" : "eoa";
}

/** Back-compat boolean: any connected wallet can now attempt payment. */
export function walletSupportsPayment() {
	return paymentWalletKind() !== "none";
}

/**
 * Read the caller's available Circle Gateway balance (raw base-unit bigint)
 * for a payment requirement's asset, at the Gateway contract named in
 * `requirements.extra.verifyingContract`. Returns null on any failure
 * (nothing connected yet, malformed requirement, RPC error) so the caller
 * can render a neutral "checking…" state rather than a false zero balance.
 */
export async function getGatewayBalance(requirements, ownerAddress) {
	const gateway = requirements?.extra?.verifyingContract;
	const token = requirements?.asset;
	if (!gateway || !token || !ownerAddress) return null;
	try {
		return await publicClient.readContract({
			address: gateway,
			abi: GATEWAY_ABI,
			functionName: "availableBalance",
			args: [token, ownerAddress],
		});
	} catch {
		return null;
	}
}

/**
 * Parse a user-typed USDC amount string ("20.00") into raw 6-decimal base
 * units. Thin wrapper over viem's parseUnits at USDC_DECIMALS — throws on
 * malformed input (scientific notation, >6 decimals, non-numeric), same as
 * DepositFlow.jsx's vault-deposit amount field; the caller shows that error.
 */
export function parseUsdcAmount(amount) {
	return parseUnits(String(amount ?? "").trim(), USDC_DECIMALS);
}

/**
 * Run the two-transaction Gateway funding flow: USDC.approve(gateway,
 * amountRaw), then Gateway.deposit(asset, amountRaw). `onProgress` is called
 * with 'approving' | 'approved' | 'depositing' | 'deposited' so the UI can
 * render a two-step stepper — mirrors DepositFlow.jsx's pattern for the
 * vault-funding path. Switches to Arc first (ensureArcChain) so a wallet
 * parked on the wrong chain doesn't send either tx somewhere the Gateway
 * contract doesn't exist. Throws (never swallows) on a wallet rejection or
 * an on-chain revert — a reverted tx still returns a receipt, so both legs
 * are receipt-checked rather than trusting the tx hash alone.
 */
export async function depositToGateway(requirements, amountRaw, onProgress = () => {}) {
	const gateway = requirements?.extra?.verifyingContract;
	const token = requirements?.asset;
	if (!gateway || !token) throw new Error("Missing Gateway contract or asset address in the payment requirements.");

	// Circle passkey path: approve + deposit as ONE batched user operation
	// through the bundler — same executor DepositFlow.jsx's vault path uses.
	if (paymentWalletKind() === "circle") {
		const smartAccount = getSmartAccount();
		const client = getSmartAccountClient();
		if (!smartAccount || !client) {
			throw new Error("Passkey wallet session expired — reconnect your Circle wallet and retry.");
		}
		onProgress("approving");
		const calls = [
			encodeCall({ address: token, abi: USDC_APPROVE_ABI, functionName: "approve", args: [gateway, amountRaw] }),
			encodeCall({ address: gateway, abi: GATEWAY_ABI, functionName: "deposit", args: [token, amountRaw] }),
		];
		onProgress("depositing");
		const out = await executeUserOp({ smartAccount, client, calls, onStateChange: () => {} });
		onProgress("deposited", { userOp: out });
		return { userOp: out };
	}

	await ensureArcChain(getRawProvider());
	const walletClient = await getWalletClient();

	onProgress("approving");
	const approveHash = await walletClient.writeContract({
		address: token,
		abi: USDC_APPROVE_ABI,
		functionName: "approve",
		args: [gateway, amountRaw],
	});
	const approveReceipt = await publicClient.waitForTransactionReceipt({ hash: approveHash });
	if (approveReceipt.status !== "success") {
		throw new Error(`USDC approval reverted on-chain (${approveHash.slice(0, 10)}…).`);
	}
	onProgress("approved", { approveHash });

	onProgress("depositing");
	const depositHash = await walletClient.writeContract({
		address: gateway,
		abi: GATEWAY_ABI,
		functionName: "deposit",
		args: [token, amountRaw],
	});
	const depositReceipt = await publicClient.waitForTransactionReceipt({ hash: depositHash });
	if (depositReceipt.status !== "success") {
		throw new Error(`Gateway deposit reverted on-chain (${depositHash.slice(0, 10)}…).`);
	}
	onProgress("deposited", { approveHash, depositHash });

	return { approveHash, depositHash };
}

/**
 * Sign the EIP-712 TransferWithAuthorization for `requirements` with the
 * connected wallet and build the Payment-Signature header string ready to
 * send on the /start retry. Switches to Arc first (ensureArcChain, same
 * helper connectWallet/reconnectWallet use) so the signed domain.chainId
 * matches the chain the wallet is actually on. Throws
 * walletSupportsPayment()'s message up front for an unsupported wallet type
 * rather than letting getWalletClient()'s passkey error surface raw.
 */
export async function signGatewayPayment({ requirements, resource, x402Version, payerAddress }) {
	const kind = paymentWalletKind();
	if (kind === "none") {
		throw new Error("No wallet connected — use Connect Wallet, then retry the payment.");
	}

	const nowSec = Math.floor(Date.now() / 1000);
	const nonceBytes = crypto.getRandomValues(new Uint8Array(32));
	const nonceHex = `0x${Array.from(nonceBytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;

	const { domain, types, primaryType, message, authorization } = buildTransferAuthorizationTypedData(
		requirements,
		{ from: payerAddress, nowSec, nonceHex },
	);

	let signature;
	if (kind === "circle") {
		// Circle passkey smart account: viem SmartAccount.signTypedData returns
		// the ERC-1271 (or ERC-6492-wrapped while counterfactual) signature in
		// the wire format Circle's own stack verifies — do NOT wrap it again
		// (the #870/#871 double-wrap lesson from the SIWE path applies here).
		const smartAccount = getSmartAccount();
		if (!smartAccount?.signTypedData) {
			throw new Error("Passkey wallet session expired — reconnect your Circle wallet and retry.");
		}
		signature = await smartAccount.signTypedData({ domain, types, primaryType, message });
	} else {
		await ensureArcChain(getRawProvider());
		const walletClient = await getWalletClient();
		signature = await walletClient.signTypedData({ domain, types, primaryType, message });
	}

	return buildPaymentSignatureHeader({ x402Version, resource, requirements, authorization, signature });
}
