import { useState, useCallback, useRef } from "react";
import { safeFetch } from "../api/safeFetch";
import { logSectionError } from "../utils/runtimeErrorLogger";

/**
 * useSectionState - セクション単位のAPI状態管理カスタムフック
 *
 * 4状態: loading / empty / error / success
 *
 * 使い方:
 *   const { state, data, error, execute, retry } = useSectionState("MomentClips");
 *
 *   useEffect(() => {
 *     execute(() => VideoService.getMomentClips(id), { videoId: id });
 *   }, [id]);
 *
 *   <SectionStateUI state={state} error={error} onRetry={retry} sectionName="Moment Clips">
 *     <DataUI data={data} />
 *   </SectionStateUI>
 */
export function useSectionState(sectionName) {
  const [state, setState] = useState("idle");   // idle | loading | success | empty | error
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);      // { type, message, status, raw }
  const lastApiFnRef = useRef(null);
  const lastOptionsRef = useRef(null);

  const execute = useCallback(async (apiFn, options = {}) => {
    const { videoId, endpoint, timeout = 30000, validate, defaultValue = null, emptyCheck } = options;
    lastApiFnRef.current = apiFn;
    lastOptionsRef.current = options;

    setState("loading");
    setError(null);

    const result = await safeFetch(apiFn, { timeout, validate, defaultValue });

    if (result.error) {
      setError(result.error);
      setState("error");
      setData(null);

      // ランタイムエラーログに記録
      logSectionError({
        sectionName,
        videoId: videoId || window.location.pathname.match(/\/video\/([^/]+)/)?.[1] || "",
        endpoint: endpoint || "",
        errorType: result.error.type,
        errorMessage: result.error.message,
        httpStatus: result.error.status,
      });

      return { data: null, error: result.error };
    }

    // カスタム空チェック
    let isEmpty = result.state === "empty";
    if (!isEmpty && typeof emptyCheck === "function") {
      isEmpty = emptyCheck(result.data);
    }

    if (isEmpty) {
      setData(result.data);
      setState("empty");
      setError(null);
      return { data: result.data, error: null };
    }

    setData(result.data);
    setState("success");
    setError(null);
    return { data: result.data, error: null };
  }, [sectionName]);

  const retry = useCallback(() => {
    if (lastApiFnRef.current && lastOptionsRef.current) {
      execute(lastApiFnRef.current, lastOptionsRef.current);
    }
  }, [execute]);

  const reset = useCallback(() => {
    setState("idle");
    setData(null);
    setError(null);
  }, []);

  return { state, data, error, execute, retry, reset, setData };
}

export default useSectionState;
