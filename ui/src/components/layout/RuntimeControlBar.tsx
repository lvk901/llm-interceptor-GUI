import React from 'react';
import {
  Circle,
  KeyRound,
  Power,
  ShieldCheck,
  Settings2,
  Square,
} from 'lucide-react';
import type { RuntimeStatus } from '../../types';
import { Tooltip } from '../common/Tooltip';

export const RuntimeControlBar: React.FC<{
  runtime: RuntimeStatus;
  isBusy: boolean;
  error: string | null;
  onAction: (action: 'proxy/start' | 'proxy/stop' | 'recording/start' | 'recording/stop' | 'certificate/install') => Promise<boolean>;
  view: 'home' | 'settings';
  onChangeView: (view: 'home' | 'settings') => void;
}> = ({ runtime, isBusy, error, onAction, view, onChangeView }) => {

  const systemProxyLabel = runtime.system_proxy.managed_by_lli ? 'System proxy active' : 'System proxy inactive';
  const certificateReady = runtime.certificate.installed;

  return (
    <header className="runtime-bar shrink-0 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-[#0b1120]">
      <div className="flex min-h-9 flex-wrap items-center gap-x-3 gap-y-2">
        <div className="mr-1 flex items-center gap-2 border-r border-slate-200 pr-3 dark:border-slate-700">
          <div className="runtime-mark" aria-hidden="true"><span /></div>
          <div className="leading-none">
            <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">LLI</div>
            <div className="mt-1 font-mono text-[10px] text-slate-500 dark:text-slate-400">LOCAL CAPTURE</div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${runtime.proxy_running ? 'bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.14)]' : 'bg-slate-300 dark:bg-slate-600'}`} />
          <span className="font-mono text-xs text-slate-600 dark:text-slate-300">
            {runtime.proxy_running ? `127.0.0.1:${runtime.proxy_port}` : 'Proxy offline'}
          </span>
        </div>

        <div className="flex items-center gap-1 border-l border-slate-200 pl-3 dark:border-slate-700">
          {runtime.proxy_running ? (
            <ActionButton label="Stop proxy" active disabled={isBusy} onClick={() => onAction('proxy/stop')}>
              <Power size={14} />
              <span>Stop proxy</span>
            </ActionButton>
          ) : (
            <ActionButton label="Start proxy" accent disabled={isBusy} onClick={() => onAction('proxy/start')}>
              <Power size={14} />
              <span>Start proxy</span>
            </ActionButton>
          )}
        </div>

        <div className="flex items-center gap-1 border-l border-slate-200 pl-3 dark:border-slate-700">
          <ActionButton
            label={runtime.recording ? 'Stop recording' : 'Start recording'}
            disabled={isBusy || !runtime.proxy_running}
            active={runtime.recording}
            onClick={() => onAction(runtime.recording ? 'recording/stop' : 'recording/start')}
          >
            {runtime.recording ? <Square size={13} fill="currentColor" /> : <Circle size={14} />}
            <span>{runtime.recording ? 'Stop recording' : 'Start recording'}</span>
          </ActionButton>
        </div>

        <div className="flex items-center gap-1 border-l border-slate-200 pl-3 dark:border-slate-700">
          <Tooltip text={systemProxyLabel}>
            <span className={`inline-flex h-7 items-center gap-1.5 px-1.5 text-xs ${runtime.system_proxy.managed_by_lli ? 'text-emerald-700 dark:text-emerald-400' : 'text-slate-400 dark:text-slate-500'}`}>
              <ShieldCheck size={15} />
              <span className="hidden sm:inline">System</span>
            </span>
          </Tooltip>
          <ActionButton
            label={certificateReady ? 'Certificate installed' : 'Install HTTPS certificate'}
            disabled={isBusy || certificateReady || !runtime.certificate.supported}
            onClick={() => onAction('certificate/install')}
          >
            <KeyRound size={14} />
            {!certificateReady && <span className="hidden lg:inline">Install certificate</span>}
          </ActionButton>
        </div>

        <div className="ml-auto border-l border-slate-200 pl-3 dark:border-slate-700">
          <ActionButton label={view === 'settings' ? 'Show sessions' : 'Open settings'} active={view === 'settings'} onClick={() => onChangeView(view === 'settings' ? 'home' : 'settings')}>
            <Settings2 size={14} />
          </ActionButton>
        </div>
      </div>
      {(runtime.error || error) && <div className="mt-2 border-l-2 border-rose-500 pl-2 text-xs text-rose-700 dark:text-rose-300">{error ?? runtime.error}</div>}
    </header>
  );
};

const ActionButton: React.FC<{
  label: string;
  children: React.ReactNode;
  disabled?: boolean;
  active?: boolean;
  accent?: boolean;
  onClick: () => void;
}> = ({ label, children, disabled = false, active = false, accent = false, onClick }) => (
  <Tooltip text={label}>
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-7 min-w-7 items-center justify-center gap-1 px-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300'
          : accent
            ? 'bg-blue-600 text-white hover:bg-blue-700'
            : 'text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white'
      }`}
    >
      {children}
    </button>
  </Tooltip>
);
