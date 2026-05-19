import { useEffect, useRef, useCallback } from "react";
import axios from "axios";

export function isRequestAborted(error) {
  return (
    axios.isCancel(error) ||
    error?.code === "ERR_CANCELED" ||
    error?.name === "CanceledError" ||
    error?.name === "AbortError"
  );
}

/**
 * Abort in-flight axios calls when the CIS comparison page unmounts or a new
 * request of the same kind starts.
 */
export function useCancellableRequests() {
  const controllersRef = useRef({});

  const begin = useCallback((key) => {
    controllersRef.current[key]?.abort();
    const controller = new AbortController();
    controllersRef.current[key] = controller;
    return controller.signal;
  }, []);

  const abort = useCallback((key) => {
    controllersRef.current[key]?.abort();
    delete controllersRef.current[key];
  }, []);

  const abortAll = useCallback(() => {
    Object.values(controllersRef.current).forEach((c) => c?.abort());
    controllersRef.current = {};
  }, []);

  useEffect(() => () => abortAll(), [abortAll]);

  return { begin, abort, abortAll };
}
