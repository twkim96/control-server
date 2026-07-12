// 화면 표시용 포맷터.

export function formatPort(port: number | null | undefined): string {
  if (typeof port !== "number") return "—";
  return String(port);
}

export function formatPid(pid: number | null | undefined): string {
  if (typeof pid !== "number") return "—";
  return String(pid);
}

export function formatCpuPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

export function formatMemory(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  if (unitIndex === 0) return `${Math.round(value)} ${units[unitIndex]}`;
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

export function formatExitCode(code: number | null | undefined): string {
  if (code === null || code === undefined) return "—";
  return String(code);
}

export function formatCommand(command: string[]): string {
  if (!command.length) return "—";
  return command
    .map((arg) => (/[\s"']/.test(arg) ? JSON.stringify(arg) : arg))
    .join(" ");
}

export function formatEnv(env: Record<string, string>): string {
  const entries = Object.entries(env);
  if (!entries.length) return "—";
  return entries.map(([k, v]) => `${k}=${v}`).join("  ");
}
