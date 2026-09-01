import { useEffect, useState } from "react";

import { apiGet } from "../api";

// ConfigService exposes these core singleton fields. The list is deliberately
// scoped to the contracts backing the claims this page actually makes —
// research, the rigor gate, and on-chain trace anchoring. Arc-native USDC and
// per-asset oracles are not fully represented either, so the derived total
// stays a floor rather than a complete inventory.
//
// The execution-side factory field is deliberately omitted: this page makes no
// execution claim at all, and the claim-integrity guard in
// ui/test/roadmap-copy.test.js asserts that literally by source-scanning this
// whole file — see EXECUTION_CLAIM_FREE_SURFACES there. That scan is why the
// words it forbids do not appear in these comments either.
const CORE_CONTRACT_FIELDS = [
	"synthetic_factory",
	"amm_router",
	"reasoning_trace_registry",
	"asset_registry",
	"price_oracle",
];
