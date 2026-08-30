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

// ── Change-window labelling (#1378) ──────────────────────────────────────────
//
// `change_24h_pct` is a one-bar change, not a 24-hour change. One bar is 24
// hours only on a 24/7 feed; a Friday-to-Monday equity pair spans 72, and a
// holiday spans more. The backend now measures the real window and sends
// `change_window_label` ("24h", "3d", ...). These helpers keep the fallback
// consistent in the four places that used to hardcode "24h".

/** Label for one asset's change window. Falls back to an unspecific, still
 * true phrase when the backend could not determine the window — never back to
 * "24h", because an unknown window must not resolve to the flattering guess. */
export function changeWindowLabel(asset) {
  return asset?.change_window_label || 'prev close'
}

/** Label shared by a group of assets, or null when they disagree.
 *
 * Only members that actually contributed to the aggregate are considered, so
 * this matches whatever the caller filtered before taking a median. A group
 * spanning a holiday can legitimately hold both "24h" and "2d" members; there
 * is no single true label for that, so the caller renders a generic one. */
export function groupChangeWindowLabel(members) {
  const labels = (members || [])
    .filter(a => a && a.change_24h_pct != null && !Number.isNaN(a.change_24h_pct))
    .map(a => a.change_window_label || null)
  if (labels.length === 0) return null
  return labels.every(l => l && l === labels[0]) ? labels[0] : null
}
