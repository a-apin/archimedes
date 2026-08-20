// Effectful half of the real x402 payment flow — Gateway balance reads, the
// approve+deposit two-transaction funding flow, and EIP-712 signing via the
// connected wallet. Kept OUT of ./generateQuote.js so that module stays a
// pure, DOM/wallet-free state machine unit-testable under node:test (see its
// own header comment) — this file is the wallet/chain-touching complement,
// mirroring the DepositFlow.jsx / circle-tx-executor.js split already
// established for vault deposits.
//
// EOA / injected-wallet path ONLY (MetaMask, Coinbase Wallet, any EIP-6963
// wallet). The Circle passkey smart-account path is explicitly out of scope
// for payments (Dan's directive): it has no viem WalletClient and can't sign
// EIP-712 today — config.js's getWalletClient() already throws a clear error
// for that path. walletSupportsPayment() below lets Generate.jsx show an
// honest "use a browser wallet" message instead of a broken pay button,
// rather than letting the throw surface as a crash.
//
// No new dependency: viem is already a dependency (see config.js), and the
// two ABIs below follow this repo's established "minimal inline ABI, just
// what's needed" convention (config.js's USDC_ABI / VAULT_ABI).

import { parseUnits } from "viem";
import {
	CIRCLE_PROVIDER_ID,
	USDC_DECIMALS,
	ensureArcChain,
	getConnectedProvider,
	getRawProvider,
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
 * True only when the connected wallet can sign EIP-712 typed data — any
 * EOA/injected path (MetaMask, Coinbase Wallet, EIP-6963 wallets). False
 * when nothing is connected, or the connected wallet is the Circle passkey
 * smart account (CIRCLE_PROVIDER_ID), which signs via Circle's bundler and
 * has no viem WalletClient. Generate.jsx checks this BEFORE offering a pay
 * button so an unsupported wallet gets an honest message instead of a click
 * that throws.
 */
export function walletSupportsPayment() {
	const provider = getConnectedProvider();
	return Boolean(provider) && provider !== CIRCLE_PROVIDER_ID;
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
	if (!walletSupportsPayment()) {
		throw new Error(
			"This wallet type can't sign payments yet — connect a browser wallet (e.g. MetaMask) to pay.",
		);
	}
	await ensureArcChain(getRawProvider());
	const walletClient = await getWalletClient();

	const nowSec = Math.floor(Date.now() / 1000);
	const nonceBytes = crypto.getRandomValues(new Uint8Array(32));
	const nonceHex = `0x${Array.from(nonceBytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;

	const { domain, types, primaryType, message, authorization } = buildTransferAuthorizationTypedData(
		requirements,
		{ from: payerAddress, nowSec, nonceHex },
	);

	const signature = await walletClient.signTypedData({ domain, types, primaryType, message });

	return buildPaymentSignatureHeader({ x402Version, resource, requirements, authorization, signature });
}
