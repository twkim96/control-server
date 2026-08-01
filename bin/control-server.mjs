#!/usr/bin/env node

import { runCli } from "../lib/cli.mjs";

try {
  process.exitCode = await runCli(process.argv.slice(2));
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`[control-server] ${message}`);
  process.exitCode = 1;
}
