import { useEffect, useState } from "react";
import { DesignWorkflowClient } from "./design-workflow.js";

export function useDesignWorkflow(sync) {
  const [view, setView] = useState({
    view: null,
    saved: null,
    error: "",
    busy: false,
  });
  const [client] = useState(
    () =>
      new DesignWorkflowClient(sync.api, (changes) =>
        setView((v) => ({ ...v, ...changes })),
      ),
  );
  useEffect(() => {
    client.active = true;
    client.reset(sync.engine?.service_instance_id);
    if (!sync.canCollaborate) return;
    client.refresh();
    const timer = setInterval(() => client.refresh(), 3000);
    return () => {
      client.stop();
      clearInterval(timer);
    };
  }, [client, sync.engine?.service_instance_id, sync.canCollaborate]);
  return {
    ...view,
    available: sync.canCollaborate,
    refresh: () => client.refresh(),
    act: (...args) => client.act(...args),
  };
}
