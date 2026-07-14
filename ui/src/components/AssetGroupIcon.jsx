// Simple inline-SVG icon set for the /explore grouped-asset cards (#464).
//
// The repo has @iconify-json/* packages declared in ui/package.json but no
// @iconify/react (or any other icon-component library) wired up to consume
// them — nothing in ui/src imports from an icon package today (checked via
// grep). Pulling in @iconify/react to use those JSON sets would be a new
// runtime dependency, which needs a human sign-off per CLAUDE.md. Plain
// inline SVGs stay dependency-free and match the existing pattern of
// hand-rolled SVGs already used for the price chart (AssetModal.jsx).

const PATHS = {
  crypto: 'M12 2 L20 7 L20 17 L12 22 L4 17 L4 7 Z M12 2 L12 22 M4 7 L20 17 M20 7 L4 17',
  fx: 'M6 8 h10 M6 8 l3 -3 M6 8 l3 3 M18 16 H8 M18 16 l-3 -3 M18 16 l-3 3',
  theme: 'M12 3 l2.5 5.5 6 0.8 -4.3 4.2 1 6 -5.2 -2.9 -5.2 2.9 1 -6 L3.5 9.3 9.5 8.5 Z',
  globe: 'M12 3 a9 9 0 1 0 0 18 a9 9 0 1 0 0 -18 M3 12 h18 M12 3 c3 3 3 15 0 18 M12 3 c-3 3 -3 15 0 18',
  chart: 'M4 20 V4 M4 20 H20 M7 17 V11 M12 17 V7 M17 17 V13',
  factor: 'M4 12 h4 M4 8 h7 M4 16 h2 M13 4 v16 M17 6 l3 3 -3 3 M17 14 l3 3 -3 3',
  sector: 'M12 12 L12 3 A9 9 0 0 1 20.5 9 Z M12 12 L20.5 9 A9 9 0 0 1 15 20.5 Z M12 12 L15 20.5 A9 9 0 1 1 12 3 Z',
  commodity: 'M4 18 L9 8 L12 13 L15 6 L20 18 Z',
  bond: 'M4 6 h16 v4 H4 Z M4 14 h16 v4 H4 Z M8 6 v4 M16 6 v4 M8 14 v4 M16 14 v4',
  metal: 'M12 2 L21 8 L18 20 H6 L3 8 Z M3 8 h18 M9 8 l3 12 M15 8 l-3 12',
  reit: 'M4 21 V10 L12 4 L20 10 V21 H14 V15 H10 V21 Z',
  volatility: 'M3 12 q2 -8 4 0 t4 0 t4 0 t4 0 q2 -8 4 0',
  default: 'M4 4 h16 v16 H4 Z M4 10 h16 M10 4 v16',
}

export default function AssetGroupIcon({ icon = 'default', size = 22 }) {
  const d = PATHS[icon] || PATHS.default
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  )
}
