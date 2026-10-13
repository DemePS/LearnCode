import type { StatRowBlock } from "../../types";

export function StatRow({ block }: { block: StatRowBlock }) {
  return (
    <div className="block block-stat-row">
      {block.stats.map((stat, i) => (
        <div className="stat" key={i}>
          <div className="stat-value">{stat.value}</div>
          <div className="stat-label">{stat.label}</div>
        </div>
      ))}
    </div>
  );
}
