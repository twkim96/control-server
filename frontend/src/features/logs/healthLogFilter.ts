export type HealthFilterLine = string | { text: string };

/** Return display lines with successful requests to the configured health path hidden. */
export function filterHealthLogLines<T extends HealthFilterLine>(
  lines: T[],
  healthUrl: string | undefined,
  showHealth = false,
): T[] {
  const healthPath = pathnameOf(healthUrl);
  if (!healthPath || showHealth) return lines;
  return lines.filter((line) => {
    const text = typeof line === "string" ? line : line.text;
    return !isSuccessfulHealthAccess(text, healthPath);
  });
}

function pathnameOf(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    return new URL(value).pathname;
  } catch {
    return undefined;
  }
}

function targetPathname(target: string): string | undefined {
  try {
    return new URL(target, "http://health-filter.invalid").pathname;
  } catch {
    return undefined;
  }
}

function isSuccessfulHealthAccess(line: string, healthPath: string): boolean {
  const json = parseJsonRecord(line);
  if (json && hasErrorSeverity(json)) return false;
  const structured = parseJsonAccess(line);
  const access = structured ?? parseCommonAccess(line);
  if (!access) return false;
  return (
    (access.method === "GET" || access.method === "HEAD") &&
    access.status >= 200 &&
    access.status < 400 &&
    targetPathname(access.target) === healthPath
  );
}

function hasErrorSeverity(record: Record<string, unknown>): boolean {
  const nested = record.log && typeof record.log === "object"
    ? record.log as Record<string, unknown>
    : undefined;
  if (record.error !== undefined && record.error !== null && record.error !== false) return true;
  return [record.level, record.severity, nested?.level, nested?.severity]
    .some((value) =>
      (typeof value === "string" && /^(error|fatal|critical)$/i.test(value)) ||
      (typeof value === "number" && value >= 50),
    ) || [record.message, nested?.message].some((value) =>
      typeof value === "string" && /\b(error|exception|failed|failure|fatal|critical)\b/i.test(value),
    );
}

interface AccessRequest {
  method: string;
  target: string;
  status: number;
}

function parseJsonAccess(line: string): AccessRequest | undefined {
  if (!line.trimStart().startsWith("{")) return undefined;
  const record = parseJsonRecord(line);
  if (!record) return undefined;
  try {
    const nestedRequest = record.request && typeof record.request === "object"
      ? record.request as Record<string, unknown>
      : undefined;
    const method = firstString(record.method, record.request_method, nestedRequest?.method);
    const status = firstNumber(record.status, record.status_code, record.statusCode);
    const target = firstString(
      record.path,
      record.request_path,
      record.request_uri,
      nestedRequest?.path,
      nestedRequest?.url,
      record.url,
    );
    const request = firstString(record.request, nestedRequest?.line);
    if (!target && request) {
      const parsed = parseRequestLine(request);
      if (parsed && status !== undefined) {
        return { ...parsed, status };
      }
    }
    if (method && status !== undefined && target) {
      return { method: method.toUpperCase(), target, status };
    }
    return parseCommonAccess(firstString(record.message) ?? "");
  } catch {
    return undefined;
  }
}

function parseJsonRecord(line: string): Record<string, unknown> | undefined {
  if (!line.trimStart().startsWith("{")) return undefined;
  try {
    const value: unknown = JSON.parse(line);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown>
      : undefined;
  } catch {
    return undefined;
  }
}

function firstString(...values: unknown[]): string | undefined {
  return values.find((value): value is string => typeof value === "string");
}

function firstNumber(...values: unknown[]): number | undefined {
  return values.find((value): value is number =>
    typeof value === "number" && Number.isFinite(value),
  );
}

function parseCommonAccess(line: string): AccessRequest | undefined {
  // Uvicorn access format: INFO:     client:port - "GET /path HTTP/1.1" 200 OK
  const uvicorn = line.match(
    /^INFO:\s+\S+\s+-\s+"(GET|HEAD)\s+(\S+)\s+HTTP\/[^"]+"\s+(\d{3})(?:\s|$)/i,
  );
  if (uvicorn) {
    return {
      method: uvicorn[1].toUpperCase(),
      target: uvicorn[2],
      status: Number(uvicorn[3]),
    };
  }
  // Common/combined log format: remote ident user [date] "METHOD target HTTP/x" status ...
  const match = line.match(
    /^\S+\s+\S+\s+\S+\s+\[[^\]]+\]\s+"(GET|HEAD)\s+(\S+)\s+HTTP\/[^"]+"\s+(\d{3})(?:\s|$)/i,
  );
  if (match) {
    return { method: match[1].toUpperCase(), target: match[2], status: Number(match[3]) };
  }
  const request = line.match(/"(GET|HEAD)\s+(\S+)\s+HTTP\/[^"]+"\s+(\d{3})(?:\s|$)/i);
  if (request && line.trimStart().startsWith("{")) {
    return {
      method: request[1].toUpperCase(),
      target: request[2],
      status: Number(request[3]),
    };
  }
  return undefined;
}

function parseRequestLine(value: string): Pick<AccessRequest, "method" | "target"> | undefined {
  const match = value.match(/^\s*"?(GET|HEAD)\s+(\S+)\s+HTTP\/[^"]+"?\s*$/i);
  return match ? { method: match[1].toUpperCase(), target: match[2] } : undefined;
}
