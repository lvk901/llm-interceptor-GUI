import type { SessionSummary } from '../types';

const sessionNameCollator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: 'base',
});

function timestampValue(timestamp: string): number {
  const value = Date.parse(timestamp);
  return Number.isNaN(value) ? 0 : value;
}

/** Keep the session list deterministic even when an API source is not pre-sorted. */
export function orderSessionSummaries(
  sessions: SessionSummary[],
  isNewestFirst: boolean
): SessionSummary[] {
  const ordered = [...sessions].sort((left, right) => {
    const timestampDifference = timestampValue(left.timestamp) - timestampValue(right.timestamp);
    if (timestampDifference !== 0) {
      return timestampDifference;
    }

    return sessionNameCollator.compare(left.id, right.id);
  });

  return isNewestFirst ? ordered.reverse() : ordered;
}
