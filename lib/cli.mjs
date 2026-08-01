import readline from "node:readline/promises";
import process from "node:process";

import {
  inspectInstallation,
  installManaged,
  migrationPlan,
  rollbackManaged,
  runLaunchd,
  uninstallManaged,
} from "./installer.mjs";
import { defaultControlHome } from "./layout.mjs";

const HELP = `Control Server 1.5 installer

Usage:
  control-server install [--home PATH] [--port PORT] [--no-start]
  control-server update [--home PATH] [--no-start]
  control-server doctor [--home PATH]
  control-server status [--home PATH]
  control-server open [--home PATH]
  control-server rollback [--home PATH] [--to VERSION] [--yes]
  control-server uninstall [--home PATH] [--purge] [--yes]
  control-server migrate --plan [--home PATH] [--source PATH]
`;

export function parseArgs(argv) {
  const first = argv[0] || "help";
  const result = {
    command: first === "--help" || first === "-h" ? "help" : first,
    home: null,
    source: null,
    to: null,
    port: null,
    start: true,
    purge: false,
    yes: false,
    plan: false,
  };
  for (let index = 1; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--home" || value === "--source" || value === "--to") {
      const next = argv[index + 1];
      if (!next) throw new Error(`${value} 값이 필요합니다.`);
      result[value.slice(2)] = next;
      index += 1;
    } else if (value === "--port") {
      const next = Number(argv[index + 1]);
      if (!Number.isInteger(next)) throw new Error("--port 값은 정수여야 합니다.");
      result.port = next;
      index += 1;
    } else if (value === "--no-start") result.start = false;
    else if (value === "--purge") result.purge = true;
    else if (value === "--yes") result.yes = true;
    else if (value === "--plan") result.plan = true;
    else if (value === "--help" || value === "-h") result.command = "help";
    else throw new Error(`알 수 없는 옵션: ${value}`);
  }
  return result;
}

async function confirm(message, yes) {
  if (yes) return true;
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error("비대화형 실행에서는 --yes가 필요합니다.");
  }
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = await rl.question(`${message} [y/N] `);
    return /^y(es)?$/i.test(answer.trim());
  } finally {
    rl.close();
  }
}

function printInspection(result) {
  console.log(`home: ${result.layout.home}`);
  console.log(`version: ${result.metadata?.version || "not installed"}`);
  for (const check of result.checks) {
    console.log(`${check.ok ? "[ok]" : "[!!]"} ${check.name}: ${check.target}`);
  }
  console.log(`${result.online ? "[ok]" : "[!!]"} HTTP: ${result.online ? "online" : result.onlineReason}`);
}

export async function runCli(argv) {
  const options = parseArgs(argv);
  const home = options.home || defaultControlHome();
  if (options.command === "help") {
    console.log(HELP);
    return 0;
  }
  if (options.command === "install" || options.command === "update") {
    const result = await installManaged({
      homeDir: home,
      port: options.port,
      start: options.start,
      mode: options.command,
    });
    console.log(`[ok] Control Server ${result.metadata.version}: ${result.layout.home}`);
    if (result.generatedPassword) {
      console.log(`초기 로그인 비밀번호: ${result.generatedPassword}`);
      console.log("이 비밀번호는 다시 출력되지 않습니다.");
      console.log(`비밀번호 저장 위치: ${result.layout.envPath} (권한 0600)`);
    }
    if (options.start) console.log(`URL: ${result.layout.url}`);
    return 0;
  }
  if (options.command === "doctor" || options.command === "status") {
    const inspection = await inspectInstallation(home);
    printInspection(inspection);
    if (options.command === "status" && inspection.metadata) {
      try {
        const launchd = runLaunchd(inspection.layout, "status");
        if (launchd.stdout) process.stdout.write(launchd.stdout);
      } catch (error) {
        console.log(`[!!] launchd: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    return inspection.checks.every((check) => check.ok) && inspection.online ? 0 : 1;
  }
  if (options.command === "open") {
    const inspection = await inspectInstallation(home);
    if (!inspection.metadata) throw new Error("설치 정보를 찾지 못했습니다.");
    const { run } = await import("./process.mjs");
    run("/usr/bin/open", [inspection.metadata.url || inspection.layout.url]);
    return 0;
  }
  if (options.command === "rollback") {
    const confirmed = await confirm(
      options.to
        ? `Control Server를 ${options.to} release로 rollback할까요?`
        : "Control Server를 직전 release로 rollback할까요?",
      options.yes,
    );
    if (!confirmed) {
      console.log("취소했습니다.");
      return 0;
    }
    const result = await rollbackManaged({ homeDir: home, toVersion: options.to });
    console.log(`[ok] rollback: ${result.fromVersion} -> ${result.toVersion}`);
    return 0;
  }
  if (options.command === "uninstall") {
    const confirmed = await confirm(
      options.purge
        ? `${home}의 앱과 config/log/runtime를 모두 삭제할까요?`
        : "관리 서비스와 Control Server를 중지하고 앱 파일을 제거할까요? config/log/runtime는 보존됩니다.",
      options.yes,
    );
    if (!confirmed) {
      console.log("취소했습니다.");
      return 0;
    }
    const result = await uninstallManaged({ homeDir: home, purge: options.purge });
    console.log(result.purged ? `[ok] 완전 삭제: ${home}` : `[ok] 앱 제거, 데이터 보존: ${home}`);
    return 0;
  }
  if (options.command === "migrate") {
    if (!options.plan) throw new Error("1.5.1에서는 migrate --plan만 지원합니다.");
    console.log(JSON.stringify(migrationPlan({ sourceRoot: options.source, homeDir: home }), null, 2));
    return 0;
  }
  throw new Error(`알 수 없는 명령: ${options.command}\n\n${HELP}`);
}
