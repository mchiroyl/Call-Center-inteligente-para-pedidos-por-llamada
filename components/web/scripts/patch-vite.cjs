const fs = require("node:fs");
const path = require("node:path");

const configPath = path.join(
  __dirname,
  "..",
  "node_modules",
  ".pnpm",
  "vite@7.3.2_@types+node@24.1_a9b943922a5d06794e35bec69efd85ce",
  "node_modules",
  "vite",
  "dist",
  "node",
  "chunks",
  "config.js",
);

if (!fs.existsSync(configPath)) {
  process.exit(0);
}

const before = fs.readFileSync(configPath, "utf8");
const after = before
  .replace(
    'import isModuleSyncConditionEnabled from "#module-sync-enabled";',
    'import isModuleSyncConditionEnabled from "../../../misc/false.js";',
  )
  .replace(
    'const isModuleSyncConditionEnabled$1 = (await import("#module-sync-enabled")).default;',
    'const isModuleSyncConditionEnabled$1 = (await import("../../../misc/false.js")).default;',
  );

if (after !== before) {
  fs.writeFileSync(configPath, after, "utf8");
}
