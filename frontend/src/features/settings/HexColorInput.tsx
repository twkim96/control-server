import { useEffect, useRef, useState } from "react";

import classes from "./settings.module.css";

const FULL_HEX_RE = /^#[0-9a-fA-F]{6}$/;
// 타이핑 중 허용: #, # + 1~6자리 hex 문자
const PARTIAL_HEX_RE = /^#[0-9a-fA-F]{0,6}$/;

export interface HexColorInputProps {
  value: string;        // 항상 valid hex (#xxxxxx). 부모 draft에서 옴.
  onChange: (value: string) => void;  // valid hex일 때만 호출.
  ariaLabel: string;
}

/**
 * 색상 hex 값을 표시 + 직접 편집할 수 있는 작은 input.
 *
 * - 외부 props.value(항상 valid hex)와 내부 textValue(편집 중 부분값 허용)를 분리.
 * - 사용자가 hex 6자리를 모두 채우면 즉시 onChange로 commit.
 * - 사용자가 hex 외 문자를 입력하려 하면 무시.
 * - blur 또는 Enter 시 부분 입력은 마지막 valid 값으로 되돌림.
 *
 * 색상 picker(input type=color)와 같은 row에서 함께 동작하므로,
 * 외부에서 picker로 값이 바뀌면 textValue도 그에 맞춰 동기화된다.
 */
export function HexColorInput({ value, onChange, ariaLabel }: HexColorInputProps) {
  const [textValue, setTextValue] = useState(value);
  const lastValidRef = useRef(value);
  const isFocusedRef = useRef(false);

  // 외부 값이 바뀌면 (예: 색상 picker로 변경, 프리셋 적용) input에도 반영.
  // 단, 사용자가 타이핑 중일 때는 외부 동기화로 덮어쓰지 않는다 — 그러면 입력이 끊긴다.
  useEffect(() => {
    if (!isFocusedRef.current) {
      setTextValue(value);
    }
    lastValidRef.current = value;
  }, [value]);

  const handleChange = (raw: string) => {
    // # 자동 보정: 사용자가 #을 안 쳤으면 앞에 붙여줌.
    let next = raw;
    if (next && !next.startsWith("#")) {
      next = "#" + next;
    }
    if (!PARTIAL_HEX_RE.test(next)) {
      // 허용되지 않는 문자 — 그대로 무시 (입력 안 됨).
      return;
    }
    setTextValue(next);
    if (FULL_HEX_RE.test(next)) {
      onChange(next.toLowerCase());
    }
  };

  const commit = () => {
    if (FULL_HEX_RE.test(textValue)) {
      const normalized = textValue.toLowerCase();
      setTextValue(normalized);
      onChange(normalized);
      return;
    }
    // 부분 입력으로 끝나면 마지막 valid 값으로 되돌림.
    setTextValue(lastValidRef.current);
  };

  return (
    <input
      type="text"
      value={textValue}
      aria-label={ariaLabel}
      spellCheck={false}
      autoCapitalize="none"
      autoCorrect="off"
      autoComplete="off"
      // 모바일 키보드 힌트 — 영숫자만.
      inputMode="text"
      maxLength={7}
      className={classes.colorHexInput}
      onFocus={() => {
        isFocusedRef.current = true;
      }}
      onBlur={() => {
        isFocusedRef.current = false;
        commit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
          (e.target as HTMLInputElement).blur();
        }
      }}
      onChange={(e) => handleChange(e.target.value)}
    />
  );
}
