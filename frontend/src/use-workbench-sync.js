import { useEffect, useState } from 'react';
import { createWorkbenchApi } from './workbench-api.js';
import { WorkbenchSync } from './workbench-sync.js';

export function useWorkbenchSync() {
  const [controller] = useState(() => new WorkbenchSync(createWorkbenchApi()));
  const [view, setView] = useState(controller.view);
  useEffect(() => {
    const unsubscribe = controller.subscribe(setView);
    controller.start();
    const resume = () => { if (document.visibilityState === 'visible') controller.reconnect(); };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', resume);
    return () => {
      unsubscribe(); controller.stop();
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('online', resume);
    };
  }, [controller]);
  return { ...view, api: controller.api, refresh: () => controller.refresh(), importModel: (file) => controller.importModel(file) };
}
