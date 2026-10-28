import { useEffect, useState } from 'react';
import { TraceController, defaultTraceSettings, matchesTrace, modelId } from './workbench-trace.js';
export function useWorkbenchTrace(sync, plane = 'YZ') {
  const [controller, setController] = useState(null);
  const [view, setView] = useState({ enabled: false, status: 'off' });
  const identity = modelId(sync.engine);
  const [chosen, setChosen] = useState(null);
  const baseSettings = chosen?.identity === identity ? chosen.settings : sync.snapshot ? defaultTraceSettings(sync.snapshot.model) : null;
  const settings = baseSettings ? { ...baseSettings, pupil_axis: plane === 'XZ' ? 'X' : 'Y' } : null;
  useEffect(() => {
    const c = new TraceController(sync.api); setController(c);
    const off = c.subscribe(setView);
    return () => { off(); c.stop(); };
  }, [sync.api]);
  const settingsKey = JSON.stringify(settings);
  useEffect(() => { controller?.update(sync.engine, sync.phase, settings); }, [controller, sync.engine, sync.phase, settingsKey]);
  const visible = view.enabled && matchesTrace(view.result, sync.engine, settings, sync.phase) ? view.result : null;
  const blocked = !sync.engine?.proposal_confirmed ? '当前项目提案尚未确认，请在 Agent 中审阅并确认'
    : sync.engine.active_project !== sync.engine.model_project ? '项目与模型归属不同'
    : !sync.engine.context_valid || sync.engine.validity !== 'valid' ? '模型状态待恢复' : '';
  return { ...view, result: visible, settings, blocked, setSettings: s=>setChosen({ identity, settings: s }),
    enable: b=>controller?.enable(b), retry: ()=>controller?.retry() };
}
