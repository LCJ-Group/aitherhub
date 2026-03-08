#!/usr/bin/env node
/**
 * Generate version.json in the build output directory.
 * This file is served as a static asset and used for deployment verification.
 *
 * Usage: node scripts/generate-version.js [output_dir]
 *
 * Environment variables (injected by CI/CD):
 *   GIT_COMMIT_SHA  - Git commit hash
 *   GIT_BRANCH      - Git branch name
 *   BUILD_TIME      - ISO timestamp of build
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const outputDir = process.argv[2] || path.join(__dirname, '..', 'dist');

// Try to get git info from environment or from git directly
function getGitInfo() {
  const commit = process.env.GIT_COMMIT_SHA || tryExec('git rev-parse --short HEAD') || 'unknown';
  const branch = process.env.GIT_BRANCH || tryExec('git rev-parse --abbrev-ref HEAD') || 'unknown';
  return { commit, branch };
}

function tryExec(cmd) {
  try {
    return execSync(cmd, { encoding: 'utf8' }).trim();
  } catch {
    return null;
  }
}

const { commit, branch } = getGitInfo();
const buildTime = process.env.BUILD_TIME || new Date().toISOString();

const versionInfo = {
  app: 'aitherhub-web',
  commit,
  branch,
  built_at: buildTime,
};

const outputPath = path.join(outputDir, 'version.json');

// Ensure output directory exists
fs.mkdirSync(outputDir, { recursive: true });

fs.writeFileSync(outputPath, JSON.stringify(versionInfo, null, 2) + '\n');

console.log(`✅ version.json generated at ${outputPath}`);
console.log(JSON.stringify(versionInfo, null, 2));
