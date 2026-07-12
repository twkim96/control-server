import { useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { PathPicker } from "./PathPicker";
import { PythonPicker } from "../services/PythonPicker";
import classes from "./service-form.module.css";

export interface BasicFields {
  id: string;
  name: string;
  description: string;
  cwd: string;
  entry_file: string;
  command: string[];  // 빈 항목 포함 가능 (UI에서)
  adopt_command: string[];
  adopt_match: "exact" | "prefix";
  port: string;        // 입력 단계에서 문자열로 다룸
  port_env_name: string;
  open_url: string;
  https_enabled: boolean;
  https_cert_file: string;
  https_key_file: string;
  https_enabled_env: string;
  https_cert_env: string;
  https_key_env: string;
  health_enabled: boolean;
  health_url: string;
  health_timeout: string;
  health_verify_ssl: boolean;
  log_tail_lines: string;
}

export interface ServiceFormFieldsProps {
  value: BasicFields;
  onChange: (next: BasicFields) => void;
  // 수정 모드일 때 id 변경 잠금
  lockId?: boolean;
}

export function ServiceFormFields({ value, onChange, lockId }: ServiceFormFieldsProps) {
  const [picker, setPicker] = useState<"cwd" | "entry" | undefined>();
  const [pythonPickerOpen, setPythonPickerOpen] = useState(false);
  // 사용자가 직접 손댄 필드는 자동 채움을 멈춘다.
  // command/entry_file은 한 번 손대면 영구 잠금이 자연스러운데,
  // open_url / health_url은 "현재 표시된 값이 자동 추론 형태인 동안엔 port 따라 계속
  // 갱신, 사용자가 다른 형태로 바꾸면 그때부터 잠금" 정책으로 따로 처리한다.
  const [touched, setTouched] = useState<Set<keyof BasicFields>>(new Set());

  const markTouched = (key: keyof BasicFields) => {
    setTouched((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  };

  // 현재 표시된 url이 어떤 port에 대해 자동 추론된 형태인지 판단.
  // 일치하면 그 port를 바꿀 때 새 자동 url로 함께 갱신할 수 있다.
  const scheme = value.https_enabled ? "https" : "http";
  const isAutoOpenUrl = (url: string, port: string): boolean =>
    url === `http://127.0.0.1:${port}` || url === `https://127.0.0.1:${port}`;
  const isAutoHealthUrl = (url: string, port: string): boolean =>
    url === `http://127.0.0.1:${port}/health` ||
    url === `https://127.0.0.1:${port}/health`;
  const defaultOpenUrl = (nextScheme: "http" | "https", port: string) =>
    port ? `${nextScheme}://127.0.0.1:${port}` : "";
  const defaultHealthUrl = (nextScheme: "http" | "https", port: string) =>
    port ? `${nextScheme}://127.0.0.1:${port}/health` : "";
  const switchScheme = (url: string, nextScheme: "http" | "https") =>
    url.replace(/^https?:\/\//, `${nextScheme}://`);

  // 패치 적용 + 다른 필드 자동 추론.
  const set = (patch: Partial<BasicFields>, source?: keyof BasicFields) => {
    if (source) markTouched(source);
    const merged: BasicFields = { ...value, ...patch };

    // command 자동 채움: entry_file이 바뀌었고, command를 사용자가 손대지 않았다면.
    if (
      "entry_file" in patch &&
      patch.entry_file &&
      !touched.has("command")
    ) {
      merged.command = ["python", "-u", patch.entry_file];
    }

    // open_url / health_url 자동 추적 정책:
    // 사용자가 직접 url을 입력으로 바꿨으면 (source === "open_url" 등) 그대로 둠.
    // 그 외에는 "현재 url이 직전 port 기준 자동 형태"이면 새 port로 다시 계산.
    // 또한 비어있는 경우엔 채워준다 (최초 한 번).
    if ("port" in patch) {
      const newPort = patch.port ?? "";
      if (newPort) {
        // open_url
        if (source !== "open_url") {
          if (!merged.open_url || isAutoOpenUrl(value.open_url, value.port)) {
            merged.open_url = defaultOpenUrl(scheme, newPort);
          }
        }
        // health_url (health_enabled일 때만)
        if (merged.health_enabled && source !== "health_url") {
          if (!merged.health_url || isAutoHealthUrl(value.health_url, value.port)) {
            merged.health_url = defaultHealthUrl(scheme, newPort);
          }
        }
      }
    }

    if ("https_enabled" in patch) {
      const nextScheme = patch.https_enabled ? "https" : "http";
      if (merged.open_url) {
        merged.open_url = switchScheme(merged.open_url, nextScheme);
      } else if (merged.port) {
        merged.open_url = defaultOpenUrl(nextScheme, merged.port);
      }
      if (merged.health_enabled) {
        if (merged.health_url) {
          merged.health_url = switchScheme(merged.health_url, nextScheme);
        } else if (merged.port) {
          merged.health_url = defaultHealthUrl(nextScheme, merged.port);
        }
      }
    }

    // health 막 켰을 때 + url 비어있고 port 있으면 한 번 채워주기.
    if (
      "health_enabled" in patch &&
      patch.health_enabled === true &&
      !merged.health_url &&
      merged.port
    ) {
      merged.health_url = defaultHealthUrl(merged.https_enabled ? "https" : "http", merged.port);
    }

    onChange(merged);
  };

  return (
    <>
      <div className={classes.section}>
        <div className={classes.sectionTitle}>기본 정보</div>
        <div className={classes.fieldRow}>
          <Field label="서비스 ID" help="영문/숫자/_/- 만 가능. 변경 불가">
            <Input
              value={value.id}
              onChange={(e) => set({ id: e.target.value })}
              disabled={lockId}
              placeholder="예: example_service"
              required
            />
          </Field>
          <Field label="표시 이름">
            <Input
              value={value.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder="예: Example Service"
              required
            />
          </Field>
        </div>
        <Field label="설명">
          <Input
            value={value.description}
            onChange={(e) => set({ description: e.target.value })}
            placeholder="간단한 한 줄 설명 (선택)"
          />
        </Field>
      </div>

      <div className={classes.section}>
        <div className={classes.sectionTitle}>실행</div>
        <Field label="working directory" help="허용 루트 안의 폴더만 선택 가능">
          <div className={classes.commandRow}>
            <Input
              value={value.cwd}
              onChange={(e) => set({ cwd: e.target.value })}
              placeholder="/path/to/server"
              required
            />
            <Button size="sm" onClick={() => setPicker("cwd")} type="button">선택</Button>
          </div>
        </Field>
        <Field label="실행 파일">
          <div className={classes.commandRow}>
            <Input
              value={value.entry_file}
              onChange={(e) => set({ entry_file: e.target.value }, "entry_file")}
              placeholder="app.py"
              required
            />
            <Button size="sm" onClick={() => setPicker("entry")} type="button">선택</Button>
          </div>
        </Field>
        <Field label="실행 명령" help="공백으로 구분된 argv. 예: `python -u app.py`">
          <div className={classes.commandRow}>
            <Input
              value={value.command.join(" ")}
              onChange={(e) =>
                set({ command: e.target.value.split(/\s+/).filter(Boolean) }, "command")
              }
              placeholder="python -u app.py"
              required
            />
            <Button size="sm" type="button" onClick={() => setPythonPickerOpen(true)}>
              Python 선택
            </Button>
          </div>
        </Field>
        <Field
          label="재입양 명령"
          help="래퍼가 최종 서버로 exec될 때만 입력. prefix는 이 명령 뒤 trailing argv만 허용합니다."
        >
          <Input
            value={value.adopt_command.join(" ")}
            onChange={(e) =>
              set({ adopt_command: e.target.value.split(/\s+/).filter(Boolean) })
            }
            placeholder="/path/to/final-server"
          />
        </Field>
        <Field label="재입양 매칭">
          <select
            className={classes.input}
            value={value.adopt_match}
            onChange={(e) => set({ adopt_match: e.target.value as "exact" | "prefix" })}
          >
            <option value="exact">exact (기본, 완전 일치)</option>
            <option value="prefix">prefix (명시한 argv 뒤 인자 허용)</option>
          </select>
        </Field>
        <div className={classes.fieldRow}>
          <Field label="포트">
            <Input
              type="number"
              value={value.port}
              onChange={(e) => set({ port: e.target.value })}
              placeholder="예: 8080"
              min={1}
              max={65535}
              // 마우스 휠로 의도치 않게 값이 바뀌는 함정 방지.
              // 사용자가 page scroll 중에 input에 hover만 해도 휠이 값을 바꾼다.
              onWheel={(e) => (e.target as HTMLInputElement).blur()}
            />
          </Field>
          <Field
            label="포트를 받을 환경변수 이름"
            help="채우면 시작 시 자식에 자동 주입. 비우면 자식의 기본값 사용."
          >
            <Input
              value={value.port_env_name}
              onChange={(e) => set({ port_env_name: e.target.value })}
              placeholder="예: APP_PORT, WORKER_PORT"
            />
          </Field>
        </div>
        <Field label="브라우저에서 열 URL" help="비워두면 더보기 메뉴에서 숨김">
          <Input
            value={value.open_url}
            onChange={(e) => set({ open_url: e.target.value }, "open_url")}
            placeholder="http://127.0.0.1:8080"
          />
        </Field>
        <Field label="프로토콜">
          <div className={classes.segmented}>
            <Button
              size="sm"
              variant={!value.https_enabled ? "primary" : "ghost"}
              onClick={() => set({ https_enabled: false })}
              type="button"
            >
              HTTP
            </Button>
            <Button
              size="sm"
              variant={value.https_enabled ? "primary" : "ghost"}
              onClick={() => set({ https_enabled: true })}
              type="button"
            >
              HTTPS
            </Button>
          </div>
        </Field>
        {value.https_enabled && (
          <>
            <div className={classes.fieldRow}>
              <Field label="cert 파일">
                <Input
                  value={value.https_cert_file}
                  onChange={(e) => set({ https_cert_file: e.target.value })}
                  placeholder=".certs/tailscale/example.crt"
                />
              </Field>
              <Field label="key 파일">
                <Input
                  value={value.https_key_file}
                  onChange={(e) => set({ https_key_file: e.target.value })}
                  placeholder=".certs/tailscale/example.key"
                />
              </Field>
            </div>
            <div className={classes.fieldRow}>
              <Field label="HTTPS env">
                <Input
                  value={value.https_enabled_env}
                  onChange={(e) => set({ https_enabled_env: e.target.value })}
                  placeholder="HTTPS"
                />
              </Field>
              <Field label="cert/key env">
                <div className={classes.commandRow}>
                  <Input
                    value={value.https_cert_env}
                    onChange={(e) => set({ https_cert_env: e.target.value })}
                    placeholder="SSL_CERT_FILE"
                  />
                  <Input
                    value={value.https_key_env}
                    onChange={(e) => set({ https_key_env: e.target.value })}
                    placeholder="SSL_KEY_FILE"
                  />
                </div>
              </Field>
            </div>
          </>
        )}
      </div>

      <div className={classes.section}>
        <div className={classes.sectionTitle}>상태 확인</div>
        <label className={classes.checkboxRow}>
          <input
            type="checkbox"
            checked={value.health_enabled}
            onChange={(e) => set({ health_enabled: e.target.checked })}
          />
          health endpoint 사용
        </label>
        {value.health_enabled && (
          <>
            <div className={classes.fieldRow}>
              <Field label="health URL">
                <Input
                  value={value.health_url}
                  onChange={(e) => set({ health_url: e.target.value }, "health_url")}
                  placeholder="http://127.0.0.1:8080/health"
                  required={value.health_enabled}
                />
              </Field>
              <Field label="timeout (초)">
                <Input
                  type="number"
                  value={value.health_timeout}
                  onChange={(e) => set({ health_timeout: e.target.value })}
                  step="0.1"
                  min={0.1}
                  onWheel={(e) => (e.target as HTMLInputElement).blur()}
                />
              </Field>
            </div>
            {value.health_url.startsWith("https://") && (
              <label className={classes.checkboxRow}>
                <input
                  type="checkbox"
                  checked={value.health_verify_ssl}
                  onChange={(e) => set({ health_verify_ssl: e.target.checked })}
                />
                SSL 인증서 검증
                <span className={classes.help} style={{ marginLeft: 8 }}>
                  self-signed cert를 쓰면 끄세요
                </span>
              </label>
            )}
          </>
        )}
      </div>

      <div className={classes.section}>
        <div className={classes.sectionTitle}>로그</div>
        <Field label="대시보드에서 보여줄 줄 수">
          <Input
            type="number"
            value={value.log_tail_lines}
            onChange={(e) => set({ log_tail_lines: e.target.value })}
            min={0}
            onWheel={(e) => (e.target as HTMLInputElement).blur()}
          />
        </Field>
      </div>

      {picker && (
        <PathPicker
          mode={picker === "cwd" ? "dir" : "file"}
          initialPath={picker === "cwd" ? value.cwd || undefined : value.cwd || undefined}
          onSelect={(p) => {
            if (picker === "cwd") {
              set({ cwd: p });
            } else {
              // entry는 cwd 기준 상대 경로로 저장하면 깔끔.
              if (value.cwd && p.startsWith(value.cwd + "/")) {
                set({ entry_file: p.slice(value.cwd.length + 1) });
              } else {
                set({ entry_file: p });
              }
            }
            setPicker(undefined);
          }}
          onCancel={() => setPicker(undefined)}
        />
      )}

      <PythonPicker
        open={pythonPickerOpen}
        onClose={() => setPythonPickerOpen(false)}
        onSelect={(path) => {
          // command 첫 토큰만 교체. 나머지 인자는 유지.
          const rest = value.command.slice(1);
          set({ command: [path, ...rest] }, "command");
          setPythonPickerOpen(false);
        }}
      />
    </>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={classes.field}>
      <div className={classes.label}>{label}</div>
      {children}
      {help && <div className={classes.help}>{help}</div>}
    </div>
  );
}
