export default function WalletGate({
	walletAddr,
	pageName,
	description,
	onConnect,
	children,
}) {
	if (walletAddr) return children;

	const titleId = `wallet-gate-${pageName.toLowerCase().replaceAll(" ", "-")}`;
	return (
		<section className="wallet-gate" aria-labelledby={titleId}>
			<div className="wallet-gate__icon" aria-hidden="true">
				<span className="i-lucide-lock" />
			</div>
			<p className="app-eyebrow">Verified wallet required</p>
			<h1 id={titleId}>Connect to view {pageName}</h1>
			<p>{description}</p>
			<button type="button" className="btn btn-primary" onClick={onConnect}>
				Connect wallet
			</button>
			<small>
				Connection requires signature proof before a wallet links to your
				account. Circle passkeys control Circle wallets only.
			</small>
		</section>
	);
}
