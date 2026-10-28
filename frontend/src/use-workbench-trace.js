import { useEffect, useState } from 'react';
import { TraceController, reconcileTraceSettings, traceBlocked, traceMessage, matchesTrace, modelId } from './workbench-trace.js';
export function useWorkbenchTrace(sync) {
  const [controller, setController] = useState(null);
  const [view, setView] = useState({ enabled: false, status: 'off' });
  const identity = modelId(sync.engine);
  const [chosen, setChosen] = useState(null);
  const model = sync.snapshot?.model;
  const selected = chosen?.identity === identity ? chosen : null;
  const reconciled = model ? reconcileTraceSettings(model,selected?.settings) : null;
  const settings = reconciled?.settings;
  const settingsAdjusted = Boolean(reconciled?.adjusted || selected?.adjusted);
  useEffect(()=>{
    if (reconciled?.adjusted) setChosen({ identity,settings,adjusted:true });
  },[identity,JSON.stringify(settings),reconciled?.adjusted]);
  useEffect(() => {
    const c = new TraceController(sync.api); setController(c);
    const off = c.subscribe(setView);
    return () => { off(); c.stop(); };
  }, [sync.api]);
  const settingsKey = JSON.stringify(settings);
  useEffect(() => { controller?.update(sync.engine, sync.phase, settings); }, [controller, sync.engine, sync.phase, settingsKey]);
  const visible = view.enabled && matchesTrace(view.result, sync.engine, settings, sync.phase) ? view.result : null;
  const blocked = traceBlocked(sync.engine,sync.phase);
  const message = traceMessage(view,sync.engine,sync.phase,settings);
  return { ...view, result: visible, settings, blocked, message, settingsAdjusted,
    setSettings: s=>setChosen({ identity, settings:{...s,pupil_axis:'Y'} }),
    enable: b=>controller?.enable(b), retry: ()=>controller?.retry() };
}
