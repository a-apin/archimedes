// The passport's source-papers header (#1769, follow-up to #1739).
//
// The header read "Fused from 5 papers" for a strategy whose five paper refs
// carried ZERO attributed mechanisms — #1739's dual-write landed the column but
// the model emitted no usable `paper_mechanisms`, so every `contribution` cell
// is an em-dash. "Fused from N" turns a citation count into a claim about
// fusion DEPTH: it tells a reader that five papers were synthesized into the
// methodology, when what is on record is that five papers were cited and none
// of them is tied to an element of the spec the strategy actually trades.
//
// The count that means something is how many cited papers name a mechanism this
// strategy trades. That number can be 0, and when it is, the header has to say
// so — this is #1636's honest-shortfall rule: a shortfall is labelled, never
// hidden and never gated on.
//
// A plain .js module, not JSX, so `node --test` executes it for real (the same
// split strategySpec.js takes — see the note at the top of
// ui/test/passport-dsl.test.js).

/** How many of `papers` carry a non-empty `contribution`. */
function attributedRows(papers) {
	let n = 0;
	for (const p of papers) if (String(p?.contribution ?? "").trim()) n++;
	return n;
}

/** The header for the source-papers table, or `null` when there are no papers.
 *
 * `distinctMechanismPapers` is the generation pipeline's own count of the same
 * thing (`FusionProposal.distinct_mechanism_papers` — cited ids that survived
 * the server-side spec_elements filter). It is not on the strategy payload
 * today; the argument is honoured so that the header uses it the day it is,
 * without a second change to this logic.
 *
 * When both are available the SMALLER wins. They are two counts of one fact and
 * the failure that matters is overclaiming: the header must never assert more
 * attribution than the table underneath it can show, and must never assert more
 * than the pipeline recorded. Taking the minimum is the only combination that
 * is safe in both directions.
 */
export function paperAttributionHeader(papers, distinctMechanismPapers) {
	const rows = Array.isArray(papers) ? papers : [];
	const cited = rows.length;
	if (cited === 0) return null;

	let attributed = attributedRows(rows);
	if (Number.isInteger(distinctMechanismPapers) && distinctMechanismPapers >= 0) {
		attributed = Math.min(attributed, distinctMechanismPapers);
	}

	const heading =
		`${cited} ${cited === 1 ? "paper" : "papers"} cited · ` +
		`${attributed} ${attributed === 1 ? "names" : "name"} a mechanism this strategy trades`;

	return { cited, attributed, heading, note: attributionNote(cited, attributed) };
}

/** The sub-line under the heading. Never empty: silence after a "0" reads as a
 * rendering bug, and the zero case is the one a reader most needs explained. */
function attributionNote(cited, attributed) {
	if (attributed === 0) {
		return (
			"None of the cited papers was attributed to any element of this strategy's spec — " +
			"they are citations, not mechanisms it is recorded as trading."
		);
	}
	if (attributed < cited) {
		return `The remaining ${cited - attributed} ${
			cited - attributed === 1 ? "paper is" : "papers are"
		} cited without an attributed mechanism.`;
	}
	return cited === 1
		? "The cited paper is tied to a named element of the spec."
		: "Every cited paper is tied to a named element of the spec.";
}
