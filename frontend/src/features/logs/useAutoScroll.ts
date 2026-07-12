import { useCallback, useEffect, useRef, useState } from "react";

// 사용자가 직접 스크롤을 위로 올리면 자동 스크롤을 끄고,
// 다시 맨 아래에 도달하면 자동 스크롤을 켠다.
export function useAutoScroll<T extends HTMLElement>(deps: unknown[]) {
  const ref = useRef<T | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const autoScrollRef = useRef(autoScroll);
  useEffect(() => {
    autoScrollRef.current = autoScroll;
  }, [autoScroll]);

  useEffect(() => {
    if (!autoScrollRef.current) return;
    const el = ref.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distanceFromBottom < 24;
    setAutoScroll(atBottom);
  }, []);

  return { ref, autoScroll, setAutoScroll, onScroll };
}
