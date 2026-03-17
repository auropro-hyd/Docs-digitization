import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

/**
 * Collapse consecutive page numbers into ranges.
 * e.g. [1,2,3,5,7,8,9] → "1–3, 5, 7–9"
 * When maxDisplay is set, truncates with a count suffix.
 */
export function formatPageRanges(
  pages: number[],
  { maxDisplay = 6, prefix = "p." }: { maxDisplay?: number; prefix?: string } = {},
): { display: string; full: string } {
  if (!pages || pages.length === 0) return { display: "", full: "" };

  const sorted = [...new Set(pages)].sort((a, b) => a - b);

  const ranges: string[] = [];
  let start = sorted[0];
  let end = sorted[0];

  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] === end + 1) {
      end = sorted[i];
    } else {
      ranges.push(start === end ? `${start}` : `${start}–${end}`);
      start = sorted[i];
      end = sorted[i];
    }
  }
  ranges.push(start === end ? `${start}` : `${start}–${end}`);

  const full = `${prefix}${ranges.join(", ")}`;

  if (ranges.length <= maxDisplay) {
    return { display: full, full };
  }

  const visible = ranges.slice(0, maxDisplay).join(", ");
  const remaining = sorted.length - ranges.slice(0, maxDisplay).reduce((sum, r) => {
    const parts = r.split("–");
    return sum + (parts.length === 2 ? Number(parts[1]) - Number(parts[0]) + 1 : 1);
  }, 0);

  return {
    display: `${prefix}${visible} (+${remaining} more)`,
    full,
  };
}
