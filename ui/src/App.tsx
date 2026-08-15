import React, { useEffect, useMemo, useState } from 'react';
import { EmptyState } from './components/layout/EmptyState';
import { ExchangeDetailsPane } from './components/layout/ExchangeDetailsPane';
import { MemoizedRequestsPane } from './components/layout/RequestsPane';
import { SessionsSidebar } from './components/layout/SessionsSidebar';
import { RuntimeControlBar } from './components/layout/RuntimeControlBar';
import { RuntimeObservabilityPanel } from './components/layout/RuntimeObservabilityPanel';
import { SettingsPage } from './components/layout/SettingsPage';
import { useAnnotations } from './hooks/useAnnotations';
import { useResizablePanels } from './hooks/useResizablePanels';
import { useSessionListPreferences } from './hooks/useSessionListPreferences';
import { useSessions } from './hooks/useSessions';
import { useTheme } from './hooks/useTheme';
import { useRuntime } from './hooks/useRuntime';
import { useRuntimeEvents } from './hooks/useRuntimeEvents';
import { stringToColor } from './utils/ui';

// API Base URL - empty for relative path (production), or localhost for dev
const API_BASE = import.meta.env.DEV ? 'http://localhost:8000' : '';

const App: React.FC = () => {
  const { isDarkMode, toggleTheme } = useTheme();
  const { isNewestFirst, toggleSortOrder } = useSessionListPreferences();
  const [systemPromptFilter, setSystemPromptFilter] = useState<string | null>(null);
  const [view, setView] = useState<'home' | 'settings'>('home');

  const {
    sessionsWidth,
    requestsWidth,
    isSessionsCollapsed,
    setIsSessionsCollapsed,
    isRequestsCollapsed,
    setIsRequestsCollapsed,
    startResizingSessions,
    startResizingRequests,
  } = useResizablePanels();

  const {
    sessionList,
    currentSession,
    isLoadingList,
    isLoadingSession,
    loadingExchangeSequenceId,
    watchStatus,
    selectedSessionId,
    setSelectedSessionId,
    selectedExchangeId,
    setSelectedExchangeId,
    deleteSession,
    selectedExchange: currentExchange,
  } = useSessions({ apiBase: API_BASE, pollMs: 2000, isNewestFirst });
  const { runtime, isAvailable: isRuntimeAvailable, isBusy: isRuntimeBusy, error: runtimeError, runAction, setSiteProfiles, updateSettings } = useRuntime({ apiBase: API_BASE });
  const observability = useRuntimeEvents({ apiBase: API_BASE, enabled: isRuntimeAvailable && !!runtime });

  const { annotations, setAnnotations, ensureLoaded, updateSessionNote, updateRequestNote } = useAnnotations({
    apiBase: API_BASE,
  });

  // Ensure annotations are loaded when session changes (fallback)
  useEffect(() => {
    if (!selectedSessionId) return;
    if (annotations[selectedSessionId]) return;
    void ensureLoaded(selectedSessionId);
  }, [annotations, ensureLoaded, selectedSessionId]);

  const filteredExchanges = useMemo(() => {
    if (!currentSession) return [];
    if (!systemPromptFilter) return currentSession.exchanges;
    return currentSession.exchanges.filter(
      (ex) => stringToColor(ex.systemPromptKey) === systemPromptFilter
    );
  }, [currentSession, systemPromptFilter]);

  const handleDeleteSession = async (sessionId: string) => {
    const deleted = await deleteSession(sessionId);
    if (deleted) {
      setAnnotations((prev) => {
        const next = { ...prev };
        delete next[sessionId];
        return next;
      });
    }

    return deleted;
  };

  return (
    <div className={`${isDarkMode ? 'dark' : ''} h-screen w-full flex flex-col bg-gray-50 dark:bg-[#0f172a] text-slate-900 dark:text-slate-200 overflow-hidden font-sans selection:bg-blue-200 dark:selection:bg-blue-500/30 transition-colors duration-200`}>
      {isRuntimeAvailable && runtime && (
        <RuntimeControlBar
          runtime={runtime}
          isBusy={isRuntimeBusy}
          error={runtimeError}
          onAction={runAction}
          view={view}
          onChangeView={setView}
        />
      )}
      <main className="flex min-h-0 flex-1">
        {view === 'settings' && runtime ? (
          <SettingsPage runtime={runtime} isBusy={isRuntimeBusy} onSetSiteProfiles={setSiteProfiles} onSaveSettings={updateSettings} />
        ) : sessionList.length === 0 ? (
          <EmptyState
            isDarkMode={isDarkMode}
            isLoadingList={isLoadingList}
            onToggleTheme={toggleTheme}
            outputDir={watchStatus?.output_dir ?? null}
            isRecording={runtime?.recording ?? watchStatus?.active ?? false}
            recordingSessionId={runtime?.session_id ?? watchStatus?.session_id ?? null}
          />
        ) : (
          <>
          <SessionsSidebar
            width={sessionsWidth}
            isCollapsed={isSessionsCollapsed}
            setIsCollapsed={setIsSessionsCollapsed}
            onStartResize={startResizingSessions}
            sessionList={sessionList}
            selectedSessionId={selectedSessionId}
            onSelectSession={setSelectedSessionId}
            isDarkMode={isDarkMode}
            onToggleTheme={toggleTheme}
            isNewestFirst={isNewestFirst}
            onToggleSortOrder={toggleSortOrder}
            annotations={annotations}
            onUpdateSessionNote={updateSessionNote}
            onDeleteSession={handleDeleteSession}
          />

          <MemoizedRequestsPane
            width={requestsWidth}
            isCollapsed={isRequestsCollapsed}
            setIsCollapsed={setIsRequestsCollapsed}
            onStartResize={startResizingRequests}
            currentSessionName={currentSession?.name}
            filteredExchanges={filteredExchanges}
            isLoadingSession={isLoadingSession}
            selectedExchangeId={selectedExchangeId}
            onSelectExchange={setSelectedExchangeId}
            systemPromptFilter={systemPromptFilter}
            setSystemPromptFilter={setSystemPromptFilter}
            selectedSessionId={selectedSessionId}
            annotations={annotations}
            onUpdateRequestNote={updateRequestNote}
          />

          <ExchangeDetailsPane
            currentExchange={currentExchange}
            sessionExchanges={filteredExchanges}
            isLoadingSession={isLoadingSession}
            isLoadingExchangeDetails={
              !!currentExchange &&
              ((!currentExchange.hasFullDetails && currentExchange.id === selectedExchangeId) ||
                loadingExchangeSequenceId === currentExchange.sequenceId)
            }
          />
          </>
        )}
      </main>
      {isRuntimeAvailable && runtime && view === 'home' && <RuntimeObservabilityPanel observability={observability} />}
    </div>
  );
};

export default App;
