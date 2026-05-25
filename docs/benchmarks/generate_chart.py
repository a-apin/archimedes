#!/usr/bin/env python3
"""Generate StockBench comparison chart — Archimedes vs. published baselines.

Produces docs/benchmarks/stockbench-vs-baselines.png
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

# Data from Chen et al. 2026 + our run
agents = [
    "Kimi-K2",
    "Qwen3-235B",
    "GLM-4.5",
    "GPT-5",
    "Claude-4-Sonnet",
    "Qwen3-32B",
    "Llama-4-Maverick",
    "DeepSeek-V3",
    "Qwen3-30B-A3B",
    "GPT-OSS-4.1",
    "Llama-3.3-70B",
    "GPT-OSS-4.1-mini",
    "DeepSeek-R1",
    "Qwen3-4B",
    "Archimedes (ours)",
]

sortino = [2.41, 2.18, 1.94, 1.87, 1.72, 1.58, 1.45, 1.39, 1.31, 1.24, 1.12, 0.98, 0.91, 0.74, -0.91]

# Colors: Archimedes in gold, others in steel blue
colors = ["#6366f1"] * 14 + ["#d4a853"]

fig, ax = plt.subplots(figsize=(12, 6))

bars = ax.barh(range(len(agents)), sortino, color=colors, edgecolor="none", height=0.7)

# Highlight Archimedes bar
bars[-1].set_edgecolor("#d4a853")
bars[-1].set_linewidth(2)

ax.set_yticks(range(len(agents)))
ax.set_yticklabels(agents, fontsize=10)
ax.invert_yaxis()
ax.set_xlabel("Sortino Ratio", fontsize=12, fontweight="bold")
ax.set_title(
    "StockBench Evaluation — Sortino Ratio\n(Chen et al. 2026, 14 baselines + Archimedes)",
    fontsize=13,
    fontweight="bold",
    pad=15,
)

# Add value labels
for i, (v, agent) in enumerate(zip(sortino, agents)):
    offset = 0.08 if v >= 0 else -0.08
    ha = "left" if v >= 0 else "right"
    ax.text(
        v + offset,
        i,
        f"{v:+.2f}",
        va="center",
        ha=ha,
        fontsize=9,
        fontweight="bold" if agent.startswith("Archimedes") else "normal",
    )

# Zero line
ax.axvline(x=0, color="#555", linewidth=0.8, linestyle="--", alpha=0.5)

# Annotation
ax.annotate(
    "Rigor gate active\n(DSR/PBO + V_check + Embargo)",
    xy=(-0.91, 14),
    xytext=(-2.5, 11),
    fontsize=8,
    fontstyle="italic",
    color="#d4a853",
    arrowprops=dict(arrowstyle="->", color="#d4a853", lw=1.2),
)

# Style
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.set_facecolor("#0d0d0d")
fig.set_facecolor("#0d0d0d")
ax.tick_params(colors="#ccc")
ax.xaxis.label.set_color("#ccc")
ax.title.set_color("#eee")
for spine in ax.spines.values():
    spine.set_color("#333")
for label in ax.get_yticklabels():
    label.set_color("#ccc")
    if "Archimedes" in label.get_text():
        label.set_color("#d4a853")
        label.set_fontweight("bold")

plt.tight_layout()
out = Path(__file__).parent / "stockbench-vs-baselines.png"
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved: {out}")
