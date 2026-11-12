import { useEffect, useState } from 'react';
import { AnalysisHistoryController, analysisHistoryScope, sameAnalysisHistoryScope, analysisHistoryConnected } from './workbench-analysis-history.js';

export function useWorkbenchAnalysisHistory(sync) {
  const [controller, setController] = useState(null);
  const [view, setView] = useState({ scope: null, status: 'idle', entries: [], left: null, right: null });
  useEffect(() => {
    const current = new AnalysisHistoryController(sync.api); setController(current);
    const unsubscribe = current.subscribe(setView);
    return () => { unsubscribe(); current.stop(); };
  }, [sync.api]);
  useEffect(() => controller?.update(sync.engine, sync.phase, sync.transportConnected), [controller, sync.engine, sync.phase, sync.transportConnected]);
  const connected = sameAnalysisHistoryScope(view.scope, analysisHistoryScope(sync.engine)) && analysisHistoryConnected(sync.engine, sync.phase, sync.transportConnected);
  const capable = Number.isSafeInteger(sync.engine?.analysis_history_revision) && sync.engine.analysis_history_revision >= 0;
  const current = connected && capable;
  return { ...view, entries: current ? view.entries : [], left: current ? view.left : null, right: current ? view.right : null,
    selected: current ? view.selected : null, compare: current ? view.compare : null,
    status: !connected ? 'offline' : !capable ? 'unavailable' : view.status,
    refresh: () => controller?.refresh(), select: id => controller?.select(id), compareWith: id => controller?.compare(id) };
}
