import { useCallback, useEffect, useState } from 'react';
import type { RuntimeStatus } from '../types';

type RuntimeAction =
  | 'proxy/start'
  | 'proxy/stop'
  | 'recording/start'
  | 'recording/stop'
  | 'certificate/install';

export function useRuntime(options: { apiBase: string; pollMs?: number }) {
  const { apiBase, pollMs = 2000 } = options;
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [isAvailable, setIsAvailable] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRuntime = useCallback(async () => {
    try {
      const response = await fetch(`${apiBase}/api/runtime`);
      if (response.status === 409) {
        setIsAvailable(false);
        setRuntime(null);
        return null;
      }
      if (!response.ok) return null;
      const data = (await response.json()) as RuntimeStatus;
      setRuntime(data);
      setIsAvailable(true);
      return data;
    } catch {
      return null;
    }
  }, [apiBase]);

  const runAction = useCallback(async (action: RuntimeAction) => {
    setIsBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/api/runtime/${action}`, { method: 'POST' });
      const payload = await response.json() as RuntimeStatus | { detail?: string };
      if (!response.ok) {
        setError('detail' in payload ? payload.detail ?? 'Operation failed' : 'Operation failed');
        return false;
      }
      setRuntime(payload as RuntimeStatus);
      return true;
    } catch {
      setError('The local desktop service is unavailable');
      return false;
    } finally {
      setIsBusy(false);
    }
  }, [apiBase]);

  const setSiteProfiles = useCallback(async (profileIds: string[]) => {
    setIsBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/api/runtime/site-profiles`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_ids: profileIds }),
      });
      const payload = await response.json() as RuntimeStatus | { detail?: string };
      if (!response.ok) {
        setError('detail' in payload ? payload.detail ?? 'Unable to update profiles' : 'Unable to update profiles');
        return false;
      }
      setRuntime(payload as RuntimeStatus);
      return true;
    } catch {
      setError('The local desktop service is unavailable');
      return false;
    } finally {
      setIsBusy(false);
    }
  }, [apiBase]);

  const updateSettings = useCallback(async (settings: { proxyPort: number; logLevel: RuntimeStatus['log_level']; addressRecognition: boolean }) => {
    setIsBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/api/runtime/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          proxy_port: settings.proxyPort,
          log_level: settings.logLevel,
          address_recognition: settings.addressRecognition,
        }),
      });
      const payload = await response.json() as RuntimeStatus | { detail?: string };
      if (!response.ok) {
        setError('detail' in payload ? payload.detail ?? 'Unable to save settings' : 'Unable to save settings');
        return false;
      }
      setRuntime(payload as RuntimeStatus);
      return true;
    } catch {
      setError('The local desktop service is unavailable');
      return false;
    } finally {
      setIsBusy(false);
    }
  }, [apiBase]);

  useEffect(() => {
    void fetchRuntime();
    const interval = window.setInterval(() => void fetchRuntime(), pollMs);
    return () => window.clearInterval(interval);
  }, [fetchRuntime, pollMs]);

  return { runtime, isAvailable, isBusy, error, runAction, setSiteProfiles, updateSettings };
}
