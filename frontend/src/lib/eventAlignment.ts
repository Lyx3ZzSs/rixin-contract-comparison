import type { PageAlignmentEntry } from '../types';

/** Returns explicit correspondence only; inserted pages never use a document ratio. */
export function counterpart(entries: PageAlignmentEntry[], page: number, side: 'original' | 'compare'): number | null {
  const from = side === 'original' ? 'original_pages' : 'compare_pages';
  const to = side === 'original' ? 'compare_pages' : 'original_pages';
  const entry = entries.find(e => e[from].includes(page));
  if (!entry || !['MATCHED', 'MERGED', 'SPLIT'].includes(entry.status) || entry.alignment_score < 0.46) return null;
  const targets = entry[to];
  if (!targets.length) return null;
  return targets[Math.min(targets.length - 1, Math.floor(entry[from].indexOf(page) / entry[from].length * targets.length))];
}
