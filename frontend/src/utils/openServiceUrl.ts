// 서비스의 open_url을 새 탭으로 띄울 때, host가 127.0.0.1 또는 localhost로 박혀
// 있으면 현재 컨트롤 대시보드에 접속한 호스트로 치환한다.
//
// 이렇게 하면 같은 config 한 줄로:
//   - 컨트롤 서버에 127.0.0.1로 접속한 경우 → 그대로 127.0.0.1로 열림
//   - Tailscale IP(100.x.y.z)로 접속한 경우 → 그 IP로 열림
//   - LAN IP(192.168.x.y)로 접속한 경우 → 그 IP로 열림
//   - MagicDNS 호스트네임으로 접속한 경우 → 그 호스트네임으로 열림
//
// 전제: 대상 서비스가 실제로 0.0.0.0(또는 외부 IF)에 listen 중이어야 한다. host가
// 127.0.0.1로만 묶인 서비스는 외부에서 잡히지 않는다 (서비스 측 host 설정 책임).

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "0.0.0.0", "::1"]);

/**
 * 주어진 url의 host가 로컬 루프백이면, 현재 페이지 hostname으로 치환한 url을 반환.
 * url 파싱 실패나 비-로컬 host면 원본을 그대로 돌려준다.
 */
export function rewriteLoopbackUrl(rawUrl: string, hostname: string): string {
  if (!rawUrl) return rawUrl;
  if (!hostname || LOCAL_HOSTS.has(hostname)) return rawUrl;
  try {
    const url = new URL(rawUrl);
    if (LOCAL_HOSTS.has(url.hostname)) {
      // IPv6 리터럴이면 [] 감싸기
      url.hostname = hostname.includes(":") ? `[${hostname}]` : hostname;
      return url.toString();
    }
    return rawUrl;
  } catch {
    return rawUrl;
  }
}

/**
 * 서비스의 open_url을 새 탭에 띄운다. 현재 접속 호스트 기준으로 loopback host를
 * 자동 치환한다.
 */
export function openServiceUrl(rawUrl: string | null | undefined): void {
  if (!rawUrl) return;
  const hostname = typeof window !== "undefined" ? window.location.hostname : "";
  const target = rewriteLoopbackUrl(rawUrl, hostname);
  window.open(target, "_blank", "noopener,noreferrer");
}
