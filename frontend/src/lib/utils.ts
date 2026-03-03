import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function getConfidenceColor(score: number): string {
  if (score >= 0.9) return "text-green-600 bg-green-50";
  if (score >= 0.7) return "text-amber-600 bg-amber-50";
  return "text-red-600 bg-red-50";
}

export function getConfidenceLabel(score: number): string {
  if (score >= 0.9) return "High";
  if (score >= 0.7) return "Medium";
  return "Low";
}

export function formatConfidence(score: number): string {
  return `${(score * 100).toFixed(1)}%`;
}
