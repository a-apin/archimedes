import { useEffect, useState } from 'react'
import { apiGet } from '../api'

// GET /api/payments/receipts — the caller's own settled generation-payment
// receipts (Dan's directive, 2026-08-21: "we must provide people with their
// receipts"). Account-session-gated (Better Auth); apiGet sends
// credentials:'include' so the session cookie reaches the endpoint and the
// backend scopes the list to the CALLER's own payments — one payer never
// sees another's charges.
//
// HONESTY (load-bearing, do not "fix"): `settlement_ref` is a Circle
// facilitator reference id, NOT an on-chain transaction hash — Circle
// batches and settles on-chain later (see
// backend/archimedes/marketplace/payments.py's module docstring and
// backend/archimedes/models/payment_receipt.py). It is rendered here as
// plain labelled text, never as a clickable arcscan link: a dead arcscan
// link for a value that was never a tx hash is worse than no link at all.

function formatReceiptDate(iso) {
	if (!iso) return "—";
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return "—";
	return d.toLocaleString(undefined, {
		year: "numeric",
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
}

export default function PaymentReceipts() {
	const [receipts, setReceipts] = useState([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");

	useEffect(() => {
		let cancelled = false;
		apiGet("/api/payments/receipts")
			.then((data) => {
				if (!cancelled) setReceipts(Array.isArray(data) ? data : []);
			})
			.catch((e) => {
				if (!cancelled) setError(e.message || "Failed to load receipts");
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, []);

	if (loading) {
		return <div className="caption">Loading payment receipts…</div>;
	}

	if (error) {
		return (
			<div className="card" style={{ padding: 18 }}>
				<p className="caption" style={{ color: "var(--negative, #ef4444)" }}>
					Could not load payment receipts: {error}
				</p>
			</div>
		);
	}

	if (receipts.length === 0) {
		return (
			<div className="card" style={{ padding: 18 }}>
				<p className="body" style={{ marginBottom: 6 }}>
					No payment receipts yet.
				</p>
				<p className="caption">
					When you pay for a generation, the receipt appears here.
				</p>
			</div>
		);
	}

	return (
		<div className="flex flex-col gap-2">
			{receipts.map((r) => (
				<div key={r.id} className="trace-card">
					<div className="flex justify-between items-center gap-3 flex-wrap">
						<strong style={{ fontSize: "0.9rem" }}>{r.price_usd}</strong>
						<span className="caption">{formatReceiptDate(r.created_at)}</span>
					</div>
					<div
						className="caption mt-1.5 flex gap-3 flex-wrap"
						style={{ color: "var(--text-3)" }}
					>
						{r.job_id && <span>job {r.job_id}</span>}
						{/* Honesty rule: labelled plain text, never an arcscan link —
						    this is a Circle facilitator reference id, not a tx hash. */}
						<span>
							Circle settlement reference:{" "}
							<span className="mono">{r.settlement_ref || "—"}</span>
						</span>
						{r.network && <span>{r.network}</span>}
					</div>
				</div>
			))}
		</div>
	);
}
