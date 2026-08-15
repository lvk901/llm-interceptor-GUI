import React, { useEffect, useRef, useState } from 'react';
import { Activity, ChevronDown, ChevronUp, Radio, TerminalSquare } from 'lucide-react';
import type { RuntimeObservability } from '../../types';

const PANEL_OPEN_STORAGE_KEY = 'lli.runtimePanel.open';

function readInitialPanelState(): boolean {
  try {
    return window.localStorage.getItem(PANEL_OPEN_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

export const RuntimeObservabilityPanel: React.FC<{ observability: RuntimeObservability }> = ({ observability }) => {
  const [isOpen, setIsOpen] = useState(readInitialPanelState);
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (terminal) terminal.scrollTop = terminal.scrollHeight;
  }, [observability.logs]);

  const heartbeatTime = observability.heartbeat_at
    ? new Date(observability.heartbeat_at).toLocaleTimeString()
    : '--:--:--';

  const togglePanel = () => {
    setIsOpen((value) => {
      const next = !value;
      try {
        window.localStorage.setItem(PANEL_OPEN_STORAGE_KEY, String(next));
      } catch {
        // The panel remains usable if browser storage is unavailable.
      }
      return next;
    });
  };

  return (
    <section className="runtime-terminal shrink-0 border-t border-slate-700 bg-[#101820] text-slate-200 dark:border-slate-700 dark:bg-[#080d13]">
      <div className="flex h-9 items-center gap-3 px-3">
        <TerminalSquare size={14} className="text-cyan-400" />
        <span className="font-mono text-[11px] font-semibold tracking-wide text-slate-100">LIVE RUNTIME</span>
        <span className="flex items-center gap-1 font-mono text-[10px] text-emerald-300">
          <Radio size={11} className="animate-pulse" /> HEARTBEAT {heartbeatTime}
        </span>
        <span className={`ml-auto font-mono text-[10px] ${observability.proxy_running ? 'text-emerald-300' : 'text-slate-500'}`}>
          {observability.proxy_running
            ? `PROXY UP ${formatDuration(observability.proxy_uptime_seconds)}`
            : 'PROXY OFF'}
        </span>
        <button
          type="button"
          aria-label={isOpen ? 'Collapse live runtime' : 'Expand live runtime'}
          aria-expanded={isOpen}
          onClick={togglePanel}
          className="inline-flex h-7 w-7 items-center justify-center text-slate-400 hover:bg-white/10 hover:text-white"
        >
          {isOpen ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
        </button>
      </div>
      {isOpen && (
        <div ref={terminalRef} className="h-44 overflow-y-auto border-t border-slate-800 px-3 py-2 font-mono text-xs leading-5">
          {observability.logs.length === 0 ? (
            <div className="flex h-full items-center gap-2 text-slate-500"><Activity size={14} /> Waiting for LLI logs</div>
          ) : observability.logs.map((entry) => (
            <div key={entry.sequence} className="grid grid-cols-[72px_64px_1fr] gap-2 whitespace-pre-wrap border-l border-transparent pl-2 hover:border-cyan-500/70 hover:bg-white/[0.025]">
              <span className="text-slate-500">{new Date(entry.timestamp).toLocaleTimeString()}</span>
              <span className={levelColor(entry.level)}>{entry.level}</span>
              <span className="break-all text-slate-200">{entry.message}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
};

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

function levelColor(level: string): string {
  if (level === 'ERROR') return 'text-rose-300';
  if (level === 'WARNING') return 'text-amber-300';
  if (level === 'DEBUG') return 'text-slate-500';
  return 'text-cyan-300';
}
