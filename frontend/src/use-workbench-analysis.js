import { useEffect,useState } from 'react';
import { AnalysisController,analysisBlocked,analysisIsCurrent } from './workbench-analysis.js';
import { modelId } from './workbench-trace.js';

export function useWorkbenchAnalysis(sync) {
  const [controller,setController]=useState(null);
  const [view,setView]=useState({status:'idle',result:null,error:'',summary:null});
  useEffect(()=>{
    const c=new AnalysisController(sync.api);setController(c);const off=c.subscribe(setView);
    return ()=>{off();c.stop();};
  },[sync.api]);
  useEffect(()=>controller?.update(sync.engine,sync.phase,sync.canAnalyze),[controller,sync.engine,sync.phase,sync.canAnalyze]);
  const visible=view.result && modelId(view.result.source)===modelId(sync.engine)?view.result:null;
  return {...view,result:visible,current:Boolean(sync.canAnalyze && analysisIsCurrent(visible,sync.engine,sync.phase)),
    blocked:analysisBlocked(sync.engine,sync.phase,sync.canAnalyze),run:s=>controller?.run(s)};
}
