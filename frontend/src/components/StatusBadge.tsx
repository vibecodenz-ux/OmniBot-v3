import { titleCase } from "../lib/format";

interface StatusBadgeProps {
  label: string;
  tone?: "success" | "warning" | "danger" | "neutral";
}

export function StatusBadge({ label, tone = "neutral" }: StatusBadgeProps) {
  return <span className={`status-badge status-${tone}`}>{titleCase(label)}</span>;
}