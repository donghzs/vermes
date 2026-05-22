// src/utility/openclaw-bootstrap.ts
var PARENT_PID_CHECK_INTERVAL = 2e3;
var PARENT_PID_ENV_KEY = "QCLAW_PARENT_PID";
var REAL_ENTRY_ENV_KEY = "QCLAW_REAL_ENTRY";
var MEMORY_FS_REGISTER_ENV_KEY = "QCLAW_MEMORY_FS_REGISTER";
var TAG = "[openclaw-bootstrap]";
var log = (...args) => console.error(TAG, ...args);
function isProcessAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
function startParentProcessWatchdog(parentPid) {
  log(`Watchdog started \u2014 monitoring parent PID ${parentPid}, interval ${PARENT_PID_CHECK_INTERVAL}ms`);
  let checkCount = 0;
  const timer = setInterval(() => {
    checkCount++;
    const alive = isProcessAlive(parentPid);
    if (checkCount % 30 === 0) {
      log(`Watchdog heartbeat #${checkCount}: parent PID ${parentPid} alive=${alive}`);
    }
    if (!alive) {
      log(`Parent process (PID: ${parentPid}) no longer alive after ${checkCount} checks, exiting.`);
      process.exit(0);
    }
  }, PARENT_PID_CHECK_INTERVAL);
  timer.unref();
}
async function main() {
  log(`Bootstrap started \u2014 PID ${process.pid}, Node ${process.version}, platform ${process.platform}-${process.arch}`);
  log(`cwd: ${process.cwd()}`);
  log(`env: ${PARENT_PID_ENV_KEY}=${process.env[PARENT_PID_ENV_KEY] ?? "(not set)"}, ${REAL_ENTRY_ENV_KEY}=${process.env[REAL_ENTRY_ENV_KEY] ?? "(not set)"}`);
  const parentPidStr = process.env[PARENT_PID_ENV_KEY];
  if (parentPidStr) {
    const parentPid = Number(parentPidStr);
    if (Number.isInteger(parentPid) && parentPid > 0) {
      log(`Parent PID ${parentPid} is valid, starting watchdog.`);
      startParentProcessWatchdog(parentPid);
    } else {
      log(`Invalid ${PARENT_PID_ENV_KEY}: "${parentPidStr}", watchdog not started.`);
    }
  } else {
    log(`${PARENT_PID_ENV_KEY} not set, parent watchdog disabled.`);
  }
  const realEntry = process.env[REAL_ENTRY_ENV_KEY];
  if (!realEntry) {
    log(`Fatal: ${REAL_ENTRY_ENV_KEY} not set, cannot start gateway.`);
    process.exit(1);
  }
  log(`Real entry path: ${realEntry}`);
  const memoryFsRegisterUrl = process.env[MEMORY_FS_REGISTER_ENV_KEY];
  if (memoryFsRegisterUrl) {
    log(`MemoryFS register URL: ${memoryFsRegisterUrl}`);
    try {
      await import(memoryFsRegisterUrl);
      log("MemoryFS register completed.");
    } catch (err) {
      const detail = err instanceof Error ? err.stack ?? err.message : String(err);
      log(`MemoryFS register FAILED: ${detail}`);
      throw err;
    }
  }
  delete process.env[PARENT_PID_ENV_KEY];
  delete process.env[REAL_ENTRY_ENV_KEY];
  delete process.env[MEMORY_FS_REGISTER_ENV_KEY];
  log("Temporary env vars cleaned.");
  const originalArgv1 = process.argv[1];
  process.argv[1] = realEntry;
  log(`process.argv[1] patched: "${originalArgv1}" \u2192 "${realEntry}"`);
  const { pathToFileURL } = await import("url");
  const entryUrl = pathToFileURL(realEntry).href;
  log(`Importing gateway entry: ${entryUrl}`);
  await import(entryUrl);
  log("Gateway entry module loaded successfully.");
}
main().catch((err) => {
  const detail = err instanceof Error ? err.stack ?? err.message : String(err);
  const message = `${TAG} Fatal error during startup: ${detail}
`;
  process.stderr.write(message);
  process.exitCode = 1;
  setTimeout(() => process.exit(1), 3e3).unref();
});
