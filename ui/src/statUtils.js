// Small shared stat helpers for display-layer aggregation (#1322).
//
// `median` exists because a group's "24h change" headline (Explore.jsx and
// AssetGroupModal.jsx) used an equal-weight arithmetic mean over the group's
// members. A single outlier — a bad tick that slips past the backend's
// plausibility guard, or simply one real large mover in a small group —
// drags an arithmetic mean by tens of points; the median is robust to a
// single outlier of any magnitude, which is the property a headline stat
// needs.

/** Median of a numeric array. Returns null for an empty array. Does not
 * mutate the input (sorts a copy). */
export function median(nums) {
  if (!nums || nums.length === 0) return null
  const sorted = [...nums].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid]
}
