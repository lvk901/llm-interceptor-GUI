import { useEffect, useState } from 'react';
import type { RuntimeObservability } from '../types';

const EMPTY_OBSERVABILITY: RuntimeObservability = {
  heartbeat_at: '',
  heartbeat_sequence: 0,
  uptime_seconds: 0,
  latest_log_sequence: 0,
  logs: [],
};

export function useRuntimeEvents(options: { apiBase: string; enabled: boolean }) {
  const { apiBase, enabled } = options;
  const [observability, setObservability] = useState<RuntimeObservability>(EMPTY_OBSERVABILITY);

  useEffect(() => {
    if (!enabled) {
      setObservability(EMPTY_OBSERVABILITY);
      return;
    }

    const source = new EventSource(`${apiBase}/api/runtime/events`);
    const onRuntimeEvent = (event: Event) => {
      const payload = JSON.parse((event as MessageEvent<string>).data) as RuntimeObservability;
      setObservability((previous) => {
        const mergedLogs = [...previous.logs, ...payload.logs]
          .filter((entry, index, entries) => index === 0 || entry.sequence !== entries[index - 1].sequence)
          .slice(-500);
        return { ...payload, logs: mergedLogs };
      });
    };

    source.addEventListener('runtime', onRuntimeEvent);
    return () => source.close();
  }, [apiBase, enabled]);

  return observability;
}
