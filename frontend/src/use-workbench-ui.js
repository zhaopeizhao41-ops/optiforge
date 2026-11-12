import { useCallback, useEffect, useRef, useState } from 'react';
import { commandDeadline, commandExpired, isAutoCommand, newUiSessionId, pageAllowed, validCommand } from './workbench-ui.js';

/**
 * Registers one frontend session per mount, heartbeats its actual page, and
 * applies navigation commands from `optiforge_workbench_navigate` only after
 * React has committed the requested page. ACK is sent from the post-commit
 * effect; the service itself can never create an applied receipt.
 *
 * A command may carry `focus` (surface / analysis tab + field / workflow tab +
 * stage). The page receives it through `requestFocus` and answers with
 * `reportFocus(id, actualFocus, reason)`; the ACK carries what was really focused.
 */
export function useWorkbenchUI(api, instanceId, page, setPage, capabilities = {}, requestFocus = () => {}, followAgent = true) {
  const followRef = useRef(followAgent);
  followRef.current = followAgent;
  const sessionId = useRef(null);
  if (!sessionId.current) sessionId.current = newUiSessionId();
  const pageRef = useRef(page);
  const pending = useRef(null);
  const active = useRef(false);
  const epoch = useRef(0);
  const navigated = useRef(false);
  const [report, setReport] = useState(null);
  const requestRef = useRef(requestFocus);
  requestRef.current = requestFocus;

  useEffect(() => {
    pageRef.current = page;
  }, [page]);

  const reportFocus = useCallback((id, actualFocus, reason = null) => {
    setReport({ id, actualFocus: actualFocus && Object.keys(actualFocus).length ? actualFocus : null, reason });
  }, []);

  useEffect(() => {
    if (!api || !instanceId) return undefined;
    active.current = true;
    const currentEpoch = epoch.current + 1;
    epoch.current = currentEpoch;
    const controller = new AbortController();
    let timer = null;
    let stopped = false;
    const isCurrent = () => active.current && epoch.current === currentEpoch && !controller.signal.aborted;

    const ack = async (command, status, actualPage, reason = null) => {
      if (!isCurrent()) return;
      try {
        await api.uiAck(instanceId, sessionId.current, command.command_id, status, actualPage, reason,
          controller.signal);
      } catch (error) {
        // The server may have expired the command; status remains the source of truth.
      }
    };

    const beat = async () => {
      const requestStart = performance.now();
      try {
        const value = await api.uiHeartbeat(instanceId, sessionId.current, pageRef.current, controller.signal);
        if (!isCurrent()) return;
        const command = value?.command;
        if (validCommand(command) && pending.current?.command.command_id !== command.command_id) {
          const deadline = commandDeadline(command, requestStart);
          if (commandExpired(deadline, performance.now())) {
            await ack(command, 'rejected', pageRef.current, 'EXPIRED');
          } else if (!pageAllowed(command.page, capabilities)) {
            await ack(command, 'rejected', pageRef.current, 'PAGE_UNAVAILABLE');
          } else if (isAutoCommand(command) && !followRef.current) {
            await ack(command, 'rejected', pageRef.current, 'FOLLOW_OFF');
          } else if (command.page === pageRef.current && !command.focus) {
            await ack(command, 'applied', pageRef.current);
          } else {
            navigated.current = true;
            pending.current = {
              command, deadline, epoch: currentEpoch, instanceId, sessionId: sessionId.current,
              signal: controller.signal,
            };
            if (command.focus) requestRef.current({ id: command.command_id, page: command.page, focus: command.focus });
            setPage(command.page);
          }
        }
      } catch (error) {
        if (!isCurrent()) return;
      }
      if (!stopped && isCurrent()) timer = setTimeout(beat, 1000);
    };

    api.uiRegister(instanceId, sessionId.current, pageRef.current, controller.signal)
      .catch(() => {})
      .then(beat);
    return () => {
      stopped = true;
      active.current = false;
      epoch.current += 1;
      pending.current = null;
      if (timer) clearTimeout(timer);
      controller.abort();
    };
  }, [api, instanceId, capabilities.canWorkflow, capabilities.canAnalyze, setPage]);

  useEffect(() => {
    const command = pending.current;
    if (!command || !active.current || command.epoch !== epoch.current) return;
    if (command.instanceId !== instanceId || command.sessionId !== sessionId.current) return;
    if (page !== command.command.page) return;
    const wantsFocus = Boolean(command.command.focus);
    if (wantsFocus && report?.id !== command.command.command_id) return;
    if (commandExpired(command.deadline, performance.now())) {
      pending.current = null;
      api.uiAck(command.instanceId, command.sessionId, command.command.command_id,
        'rejected', page, 'EXPIRED', command.signal).catch(() => {});
      return;
    }
    pending.current = null;
    api.uiAck(command.instanceId, command.sessionId, command.command.command_id,
      'applied', page, wantsFocus ? report.reason : null, command.signal,
      wantsFocus ? report.actualFocus : null).catch(() => {});
  }, [page, instanceId, api, report]);

  return { navigated, reportFocus };
}
