import React, { useState } from 'react';
import { Check, FolderOpen, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import type { RuntimeStatus } from '../../types';

export const SettingsPage: React.FC<{
  runtime: RuntimeStatus;
  isBusy: boolean;
  onSetSiteProfiles: (profileIds: string[]) => Promise<boolean>;
  onSaveSettings: (settings: { proxyPort: number; logLevel: RuntimeStatus['log_level']; addressRecognition: boolean }) => Promise<boolean>;
}> = ({ runtime, isBusy, onSetSiteProfiles, onSaveSettings }) => {
  const [proxyPort, setProxyPort] = useState<number | null>(null);
  const [logLevel, setLogLevel] = useState<RuntimeStatus['log_level'] | null>(null);
  const [addressRecognition, setAddressRecognition] = useState<boolean | null>(null);
  const [saved, setSaved] = useState(false);

  const currentProxyPort = proxyPort ?? runtime.proxy_port;
  const currentLogLevel = logLevel ?? runtime.log_level;
  const currentAddressRecognition = addressRecognition ?? runtime.address_recognition;

  const toggleProfile = (id: string) => {
    const enabled = new Set(runtime.enabled_site_profiles);
    if (enabled.has(id)) enabled.delete(id);
    else enabled.add(id);
    void onSetSiteProfiles([...enabled]);
  };

  const save = async () => {
    const wasSaved = await onSaveSettings({
      proxyPort: currentProxyPort,
      logLevel: currentLogLevel,
      addressRecognition: currentAddressRecognition,
    });
    setSaved(wasSaved);
    if (wasSaved) {
      setProxyPort(null);
      setLogLevel(null);
      setAddressRecognition(null);
      window.setTimeout(() => setSaved(false), 1800);
    }
  };

  return (
    <section className="min-w-0 flex-1 overflow-y-auto bg-white px-5 py-6 dark:bg-[#0f172a] sm:px-8">
      <div className="mx-auto max-w-5xl">
        <header className="mb-8 flex items-start justify-between border-b border-slate-200 pb-5 dark:border-slate-800">
          <div>
            <div className="font-mono text-[11px] font-semibold tracking-wide text-cyan-700 dark:text-cyan-300">DESKTOP CONFIGURATION</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-900 dark:text-white">Settings</h1>
          </div>
          <button type="button" onClick={() => void save()} disabled={isBusy || !Number.isInteger(currentProxyPort) || currentProxyPort < 1 || currentProxyPort > 65535} className="inline-flex h-9 items-center gap-2 bg-blue-600 px-3 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40">
            <Check size={15} /> {saved ? 'Saved' : 'Save changes'}
          </button>
        </header>

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_260px]">
          <div className="space-y-8">
            <SettingsSection title="Proxy">
              <label className="grid max-w-xs gap-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                Listening port
                <input type="number" min="1" max="65535" value={currentProxyPort} disabled={runtime.proxy_running || isBusy} onChange={(event) => setProxyPort(Number(event.target.value))} className="h-9 border border-slate-300 bg-white px-2 font-mono text-sm outline-none focus:border-blue-500 disabled:cursor-not-allowed disabled:bg-slate-100 dark:border-slate-700 dark:bg-slate-950 dark:disabled:bg-slate-900" />
              </label>
              <label className="mt-4 grid max-w-xs gap-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                Log level
                <select value={currentLogLevel} disabled={isBusy} onChange={(event) => setLogLevel(event.target.value as RuntimeStatus['log_level'])} className="h-9 border border-slate-300 bg-white px-2 text-sm outline-none focus:border-blue-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950">
                  <option value="DEBUG">Debug</option>
                  <option value="INFO">Info</option>
                  <option value="WARNING">Warning</option>
                  <option value="ERROR">Error</option>
                </select>
              </label>
            </SettingsSection>

            <SettingsSection title="Model websites">
              <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
                {runtime.site_profiles.map((profile) => (
                  <label key={profile.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
                    <input type="checkbox" checked={runtime.enabled_site_profiles.includes(profile.id)} disabled={runtime.proxy_running || isBusy} onChange={() => toggleProfile(profile.id)} className="h-4 w-4 accent-blue-600" />
                    {profile.name}
                  </label>
                ))}
              </div>
            </SettingsSection>

            <SettingsSection title="Relay recognition">
              <label className="flex max-w-xl items-center gap-3 text-sm text-slate-700 dark:text-slate-200">
                <input
                  type="checkbox"
                  checked={currentAddressRecognition}
                  disabled={isBusy}
                  onChange={(event) => setAddressRecognition(event.target.checked)}
                  className="h-4 w-4 accent-blue-600"
                />
                Address recognition (standard LLM endpoints and compatible request bodies)
              </label>
            </SettingsSection>

          </div>

          <aside className="border-l border-slate-200 pl-5 dark:border-slate-800">
            <div className="space-y-5 text-sm">
              <StatusLine icon={<ShieldCheck size={16} />} label="System proxy" value={runtime.system_proxy.managed_by_lli ? 'Active' : 'Inactive'} positive={runtime.system_proxy.managed_by_lli} />
              <StatusLine icon={<ShieldCheck size={16} />} label="HTTPS certificate" value={runtime.certificate.installed ? 'Installed' : 'Required'} positive={runtime.certificate.installed} />
              <StatusLine icon={<FolderOpen size={16} />} label="Session storage" value={runtime.output_dir} />
              <StatusLine icon={<SlidersHorizontal size={16} />} label="Active profiles" value={`${runtime.enabled_site_profiles.length} / ${runtime.site_profiles.length}`} />
              <StatusLine icon={<SlidersHorizontal size={16} />} label="Relay recognition" value={runtime.address_recognition ? 'Enabled' : 'Disabled'} positive={runtime.address_recognition} />
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
};

const SettingsSection: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section className="border-b border-slate-200 pb-7 dark:border-slate-800">
    <h2 className="mb-4 text-sm font-semibold text-slate-900 dark:text-white">{title}</h2>
    {children}
  </section>
);

const StatusLine: React.FC<{ icon: React.ReactNode; label: string; value: string; positive?: boolean }> = ({ icon, label, value, positive }) => (
  <div>
    <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400">{icon}<span>{label}</span></div>
    <div className={`mt-1 break-all pl-6 font-mono text-xs ${positive ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-700 dark:text-slate-200'}`}>{value}</div>
  </div>
);
