import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';

type ResizablePanel = 'sessions' | 'requests';

const COLLAPSED_PANEL_WIDTH = 48;
const MIN_DETAILS_WIDTH = 360;
const SESSIONS_MIN_WIDTH = 200;
const SESSIONS_MAX_WIDTH = 600;
const REQUESTS_MIN_WIDTH = 250;
const REQUESTS_MAX_WIDTH = 600;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

export function useResizablePanels(options?: {
  sessionsWidthInitial?: number;
  requestsWidthInitial?: number;
}) {
  const [sessionsWidth, setSessionsWidth] = useState(options?.sessionsWidthInitial ?? 280);
  const [requestsWidth, setRequestsWidth] = useState(options?.requestsWidthInitial ?? 320);
  const [isSessionsCollapsed, setIsSessionsCollapsed] = useState(false);
  const [isRequestsCollapsed, setIsRequestsCollapsed] = useState(false);
  const [isResizing, setIsResizing] = useState<ResizablePanel | null>(null);

  const layoutRef = useRef<HTMLDivElement>(null);
  const sessionsWidthRef = useRef(sessionsWidth);
  const requestsWidthRef = useRef(requestsWidth);
  const sessionsCollapsedRef = useRef(isSessionsCollapsed);
  const requestsCollapsedRef = useRef(isRequestsCollapsed);
  const containerWidthRef = useRef(0);
  const resizeRef = useRef<{
    panel: ResizablePanel;
    pointerId: number;
    startX: number;
    startWidth: number;
  } | null>(null);
  const cleanupResizeRef = useRef<(() => void) | null>(null);
  const pendingResizeRef = useRef<{ panel: ResizablePanel; width: number } | null>(null);
  const animationFrameRef = useRef<number | null>(null);

  useEffect(() => {
    sessionsWidthRef.current = sessionsWidth;
  }, [sessionsWidth]);

  useEffect(() => {
    requestsWidthRef.current = requestsWidth;
  }, [requestsWidth]);

  useEffect(() => {
    sessionsCollapsedRef.current = isSessionsCollapsed;
  }, [isSessionsCollapsed]);

  useEffect(() => {
    requestsCollapsedRef.current = isRequestsCollapsed;
  }, [isRequestsCollapsed]);

  const getBounds = useCallback((panel: ResizablePanel) => {
    const isSessionsPanel = panel === 'sessions';
    const minimum = isSessionsPanel ? SESSIONS_MIN_WIDTH : REQUESTS_MIN_WIDTH;
    const maximum = isSessionsPanel ? SESSIONS_MAX_WIDTH : REQUESTS_MAX_WIDTH;
    const siblingWidth = isSessionsPanel
      ? (requestsCollapsedRef.current ? COLLAPSED_PANEL_WIDTH : requestsWidthRef.current)
      : (sessionsCollapsedRef.current ? COLLAPSED_PANEL_WIDTH : sessionsWidthRef.current);
    const availableWidth = containerWidthRef.current - siblingWidth - MIN_DETAILS_WIDTH;

    return {
      minimum,
      maximum: Math.max(minimum, Math.min(maximum, availableWidth || maximum)),
    };
  }, []);

  const setPanelWidth = useCallback((panel: ResizablePanel, requestedWidth: number) => {
    const { minimum, maximum } = getBounds(panel);
    const width = Math.round(clamp(requestedWidth, minimum, maximum));

    if (panel === 'sessions') {
      if (sessionsWidthRef.current !== width) {
        sessionsWidthRef.current = width;
        setSessionsWidth(width);
      }
      return;
    }

    if (requestsWidthRef.current !== width) {
      requestsWidthRef.current = width;
      setRequestsWidth(width);
    }
  }, [getBounds]);

  const commitPendingResize = useCallback(() => {
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }

    const pendingResize = pendingResizeRef.current;
    pendingResizeRef.current = null;
    if (pendingResize) {
      setPanelWidth(pendingResize.panel, pendingResize.width);
    }
  }, [setPanelWidth]);

  const scheduleResize = useCallback((panel: ResizablePanel, width: number) => {
    pendingResizeRef.current = { panel, width };
    if (animationFrameRef.current !== null) {
      return;
    }

    animationFrameRef.current = window.requestAnimationFrame(() => {
      animationFrameRef.current = null;
      const pendingResize = pendingResizeRef.current;
      pendingResizeRef.current = null;
      if (pendingResize) {
        setPanelWidth(pendingResize.panel, pendingResize.width);
      }
    });
  }, [setPanelWidth]);

  const stopResizing = useCallback((pointerId?: number) => {
    const activeResize = resizeRef.current;
    if (!activeResize || (pointerId !== undefined && activeResize.pointerId !== pointerId)) {
      return;
    }

    commitPendingResize();
    cleanupResizeRef.current?.();
    cleanupResizeRef.current = null;
    resizeRef.current = null;
    setIsResizing(null);
  }, [commitPendingResize]);

  const startResizing = useCallback((panel: ResizablePanel, event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) {
      return;
    }

    stopResizing();
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);

    const startWidth = panel === 'sessions' ? sessionsWidthRef.current : requestsWidthRef.current;
    resizeRef.current = {
      panel,
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth,
    };
    setIsResizing(panel);

    const onPointerMove = (moveEvent: PointerEvent) => {
      const activeResize = resizeRef.current;
      if (!activeResize || activeResize.pointerId !== moveEvent.pointerId) {
        return;
      }

      scheduleResize(
        activeResize.panel,
        activeResize.startWidth + (moveEvent.clientX - activeResize.startX)
      );
    };
    const onPointerEnd = (endEvent: PointerEvent) => stopResizing(endEvent.pointerId);

    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerEnd);
    document.addEventListener('pointercancel', onPointerEnd);
    cleanupResizeRef.current = () => {
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerEnd);
      document.removeEventListener('pointercancel', onPointerEnd);
    };
  }, [scheduleResize, stopResizing]);

  useEffect(() => {
    const element = layoutRef.current;
    if (!element) {
      return;
    }

    const updateContainerWidth = () => {
      const width = element.clientWidth;
      containerWidthRef.current = width;
      window.requestAnimationFrame(() => {
        setPanelWidth('sessions', sessionsWidthRef.current);
        setPanelWidth('requests', requestsWidthRef.current);
      });
    };

    updateContainerWidth();
    const observer = new ResizeObserver(updateContainerWidth);
    observer.observe(element);
    return () => observer.disconnect();
  }, [setPanelWidth]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setPanelWidth('sessions', sessionsWidthRef.current);
      setPanelWidth('requests', requestsWidthRef.current);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [isRequestsCollapsed, isSessionsCollapsed, setPanelWidth]);

  useEffect(() => () => {
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
    }
    pendingResizeRef.current = null;
    cleanupResizeRef.current?.();
  }, []);

  const startResizingSessions = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => startResizing('sessions', event),
    [startResizing]
  );
  const startResizingRequests = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => startResizing('requests', event),
    [startResizing]
  );

  return {
    layoutRef,
    sessionsWidth,
    setSessionsWidth,
    requestsWidth,
    setRequestsWidth,
    isSessionsCollapsed,
    setIsSessionsCollapsed,
    isRequestsCollapsed,
    setIsRequestsCollapsed,
    startResizingSessions,
    startResizingRequests,
    isResizing,
  };
}
