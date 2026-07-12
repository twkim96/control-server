// 시간 포맷 헬퍼. 모든 입력은 epoch 초 또는 초 단위 duration.

export function formatUptime(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || seconds < 0) return "—";
  const total = Math.floor(seconds);
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;

  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatRelative(epoch: number | null | undefined, now = Date.now() / 1000): string {
  if (typeof epoch !== "number") return "—";
  const diff = now - epoch;
  if (diff < 0) return "방금";
  if (diff < 60) return `${Math.floor(diff)}초 전`;
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}

export function formatTime(epoch: number | null | undefined): string {
  if (typeof epoch !== "number") return "—";
  const date = new Date(epoch * 1000);
  return date.toLocaleString("ko-KR", {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
