// Per-section disclaimer for Quant Lab panels that render synthetic series
// (#1060). Render next to a section label whenever the data shown does not
// come from a live vault or backtest; callers omit it once real data arrives
// via props or API, so the badge disappears on its own when a panel is wired.
export default function SampleDataBadge() {
  return (
    <span
      className="tag tag-warning"
      title="Synthetic sample data — not from a live vault or backtest."
      style={{ marginLeft: 8, verticalAlign: 'middle', textTransform: 'none', letterSpacing: 0 }}
    >
      Illustrative sample data
    </span>
  )
}
