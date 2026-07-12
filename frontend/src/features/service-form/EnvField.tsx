import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { IconButton } from "../../components/IconButton";
import classes from "./service-form.module.css";

export interface EnvFieldProps {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}

interface Pair {
  key: string;
  value: string;
}

function fromMap(map: Record<string, string>): Pair[] {
  return Object.entries(map).map(([key, value]) => ({ key, value }));
}

function toMap(pairs: Pair[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { key, value } of pairs) {
    if (key) out[key] = value;
  }
  return out;
}

// UI에서는 입력 중인 빈 key/value 행도 보존해야 한다.
// 빈 key는 toMap에서 부모로 올려보내지 않을 뿐, 로컬 상태에는 남긴다.
export function EnvField({ value, onChange }: EnvFieldProps) {
  const [pairs, setPairs] = useState<Pair[]>(() => fromMap(value));

  // 외부에서 value가 바뀌면(폼 reset 또는 edit 모드 초기 로드) 동기화.
  // 단, 우리가 방금 onChange로 푸시한 상태와 동일하면 무시한다.
  const lastPushed = useRef<Record<string, string> | null>(null);
  useEffect(() => {
    if (lastPushed.current && shallowEq(lastPushed.current, value)) return;
    setPairs(fromMap(value));
  }, [value]);

  const update = (next: Pair[]) => {
    setPairs(next);
    const mapped = toMap(next);
    lastPushed.current = mapped;
    onChange(mapped);
  };

  return (
    <div className={classes.field}>
      <div className={classes.label}>환경변수</div>
      {pairs.length === 0 && (
        <div className={classes.envEmpty}>등록된 환경변수가 없습니다.</div>
      )}
      {pairs.map((p, idx) => (
        <div key={idx} className={classes.envItem}>
          <Input
            placeholder="KEY"
            value={p.key}
            onChange={(e) => {
              const next = [...pairs];
              next[idx] = { ...p, key: e.target.value };
              update(next);
            }}
          />
          <Input
            placeholder="value"
            value={p.value}
            onChange={(e) => {
              const next = [...pairs];
              next[idx] = { ...p, value: e.target.value };
              update(next);
            }}
          />
          <IconButton
            label="삭제"
            onClick={() => {
              const next = pairs.filter((_, i) => i !== idx);
              update(next);
            }}
          >
            <TrashIcon />
          </IconButton>
        </div>
      ))}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => update([...pairs, { key: "", value: "" }])}
      >
        + 변수 추가
      </Button>
    </div>
  );
}

function shallowEq(a: Record<string, string>, b: Record<string, string>) {
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) if (a[k] !== b[k]) return false;
  return true;
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M2 4 H12 M5 4 V2 H9 V4 M3.5 4 L4 12 H10 L10.5 4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
