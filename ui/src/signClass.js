// Shared sign-based CSS class helper (#1361).
//
// Colour must be derived from the *value's own sign*, never hard-coded at the
// call site — that was the defect: the Library table painted CAGR and the
// "$1k ->" projection profit-green unconditionally, so a strategy that lost
// money still rendered green under a header reading "$1k ->".
//
// Same rule, same class names as `changeClass()` in AssetModal.jsx /
// AssetGroupModal.jsx (`v >= 0 ? 'positive' : 'negative'`, `''` for
// null/NaN so an unknown value gets no colour rather than a guessed one).
// Strategies.jsx imports this rather than adding a third hand-rolled copy.
export function signClass(v) {
	if (v == null || Number.isNaN(v)) return ""
	return v >= 0 ? "positive" : "negative"
}
