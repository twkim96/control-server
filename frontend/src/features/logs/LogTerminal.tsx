import { memo } from "react";

import type { LogLineEntry } from "../../hooks/useBufferedLogLines";
import { useAutoScroll } from "./useAutoScroll";
import classes from "./logs.module.css";

export type LogTerminalLine = string | LogLineEntry;

export interface LogTerminalProps {
  lines: LogTerminalLine[];
}

export function LogTerminal({ lines }: LogTerminalProps) {
  const { ref, onScroll } = useAutoScroll<HTMLDivElement>([lines.length]);

  return (
    <div className={classes.terminal} ref={ref} onScroll={onScroll}>
      {lines.length === 0 ? (
        <div className={classes.terminalEmpty}>로그가 비어있습니다…</div>
      ) : (
        lines.map((line, i) => {
          const entry = normalizeLine(line, i);
          return <TerminalLine key={entry.key} text={entry.text} />;
        })
      )}
    </div>
  );
}

function normalizeLine(line: LogTerminalLine, index: number): { key: string; text: string } {
  if (typeof line === "string") {
    return { key: `static-${index}`, text: line };
  }
  return { key: `live-${line.id}`, text: line.text };
}

const TerminalLine = memo(function TerminalLine({ text }: { text: string }) {
  return <div className={classes.terminalLine}>{text}</div>;
});
