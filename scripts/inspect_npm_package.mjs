import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "control-server-pack-"));
try {
  const prepared = spawnSync("node", ["scripts/prepare_npm_package.mjs"], {
    stdio: "inherit",
  });
  if (prepared.status !== 0) process.exit(prepared.status || 1);
  const packed = spawnSync("npm", ["pack", "--json", "--ignore-scripts", "--pack-destination", temporary], {
    encoding: "utf8",
    stdio: "pipe",
    env: { ...process.env, NPM_CONFIG_CACHE: path.join(temporary, "npm-cache") },
  });
  if (packed.status !== 0) {
    process.stderr.write(packed.stderr || packed.stdout || "npm pack failed\n");
    process.exit(packed.status || 1);
  }
  const metadata = JSON.parse(packed.stdout);
  const item = metadata[0];
  const names = item.files.map((entry) => entry.path);
  const required = [
    "bin/control-server.mjs",
    "backend/app.py",
    "backend/requirements-runtime.lock",
    "frontend/dist/index.html",
    "ops/pm2/npm-shrinkwrap.json",
    "launchd/install.sh",
    "LICENSE",
    "LICENSES/PRETENDARD-OFL-1.1.txt",
  ];
  const missing = required.filter((name) => !names.includes(name));
  const forbidden = names.filter((name) =>
    /(^|\/)(node_modules|tests|logs|runtime)(\/|$)|(^|\/)config\.yml$|(^|\/)run\.env$|requirements-dev\.txt$|backend\/conftest\.py$|frontend\/(?:src|README\.md)(?:\/|$)|\.npmignore$|devspace/i.test(name),
  );
  if (missing.length || forbidden.length) {
    throw new Error(
      `invalid package contents; missing=${missing.join(",") || "none"}; forbidden=${forbidden.join(",") || "none"}`,
    );
  }
  const archive = path.join(temporary, item.filename);
  const extracted = path.join(temporary, "extracted");
  fs.mkdirSync(extracted);
  const untar = spawnSync("tar", ["-xzf", archive, "-C", extracted], {
    encoding: "utf8",
    stdio: "pipe",
  });
  if (untar.status !== 0) {
    throw new Error(untar.stderr || "failed to inspect package archive");
  }
  const sensitivePatterns = [
    /\/Users\/(?!(?:example|\.\.\.)(?:\/|$))[^/\s]+/,
    /\/home\/twkim(?:\/|$)/,
    /FILE_CHECK_/,
    /taila[0-9a-z]+\.ts\.net/i,
    /ngrok-free\.app/i,
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
    /\bgh[pousr]_[A-Za-z0-9_]{20,}\b/,
  ];
  const findings = [];
  const scan = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        scan(target);
        continue;
      }
      if (/\.(?:woff2?|png|jpe?g|gif|ico)$/i.test(entry.name)) continue;
      const body = fs.readFileSync(target, "utf8");
      if (sensitivePatterns.some((pattern) => pattern.test(body))) {
        findings.push(path.relative(extracted, target));
      }
    }
  };
  scan(extracted);
  if (findings.length) {
    throw new Error(`sensitive package content: ${findings.join(", ")}`);
  }
  console.log(JSON.stringify({ filename: item.filename, size: item.size, unpackedSize: item.unpackedSize, files: item.entryCount }, null, 2));
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
