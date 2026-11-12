import { useEffect, useState } from 'react';
import { HistoryController, historyScope, sameHistoryScope, historyConnected } from './workbench-history.js';

export function useWorkbenchHistory(sync) {
  const [controller,setController] = useState(null);
  const [view,setView] = useState({scope:null,status:'idle',entries:[],left:null,right:null});
  useEffect(() => {
    const current = new HistoryController(sync.api); setController(current);
    const unsubscribe = current.subscribe(setView);
    return () => { unsubscribe(); current.stop(); };
  },[sync.api]);
  useEffect(() => controller?.update(sync.engine,sync.phase,sync.error),[controller,sync.engine,sync.phase,sync.error]);
  // The render guard hides an old scope before the effect can abort its requests.
  const current = sameHistoryScope(view.scope,historyScope(sync.engine)) && historyConnected(sync.engine,sync.phase,sync.error);
  return {...view, entries:current?view.entries:[], left:current?view.left:null, right:current?view.right:null,
    selected:current?view.selected:null, compare:current?view.compare:null, status:current?view.status:'offline',
    refresh:() => controller?.refresh(), select:id => controller?.select(id), compareWith:id => controller?.compare(id)};
}
