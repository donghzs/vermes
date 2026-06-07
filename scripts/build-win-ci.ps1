# Vermes Windows CI/CD Build Script — Runs on A11
# Triggered from Mac via WinRM

Write-Host "═══════════════════════════════════════════"
Write-Host "  Vermes Windows Build Pipeline"
Write-Host "═══════════════════════════════════════════"

$ErrorActionPreference = "Stop"
$ROOT = "C:\Projects\vermes-electron"
$NODE = "C:\Program Files\nodejs"
$PYTHON = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
$env:PATH = "$NODE;$env:PATH"

# Step 0: Clean
Write-Host "`nStep 0: Clean old build artifacts"
Remove-Item -Recurse -Force "$ROOT\dist" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$ROOT\dist-electron" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$ROOT\build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$ROOT\frontend\dist" -ErrorAction SilentlyContinue
Write-Host "  Done"

# Step 1: Verify tools
Write-Host "`nStep 1: Verify tools"
& "$NODE\node.exe" --version
& "$NODE\npm.cmd" --version
& $PYTHON --version
Write-Host "  Done"

# Step 2: Install frontend deps
Write-Host "`nStep 2: Install frontend deps"
Push-Location "$ROOT\frontend"
& "$NODE\npm.cmd" ci 2>&1 | Select-Object -Last 2
Pop-Location
Write-Host "  Done"

# Step 3: Install electron deps  
Write-Host "`nStep 3: Install electron deps"
Push-Location "$ROOT\electron"
& "$NODE\npm.cmd" ci 2>&1 | Select-Object -Last 2
Pop-Location
Write-Host "  Done"

# Step 4: Build frontend
Write-Host "`nStep 4: Build frontend"
Push-Location "$ROOT\frontend"
& "$NODE\npm.cmd" run build
Pop-Location
Write-Host "  Done"

# Step 5: Sync web_dist
Write-Host "`nStep 5: Sync web_dist"
Copy-Item -Force "$ROOT\frontend\dist\*" "$ROOT\hermes_cli\web_dist\" -Recurse
# Verify
$js = Get-ChildItem "$ROOT\hermes_cli\web_dist\assets\index-*.js" | Select-Object -First 1
Write-Host "  JS: $($js.Name)"
Write-Host "  Done"

# Step 6: Install Python deps
Write-Host "`nStep 6: Install Python deps"
& $PYTHON -m pip install --upgrade pip --quiet 2>&1 | Select-Object -Last 1
& $PYTHON -m pip install pyinstaller uvicorn fastapi starlette httpx pyyaml aiofiles --quiet 2>&1 | Select-Object -Last 1
Write-Host "  Done"

# Step 7: PyInstaller build
Write-Host "`nStep 7: PyInstaller build"
Push-Location "$ROOT"
& $PYTHON -m PyInstaller vermes-backend.spec --noconfirm
Pop-Location
$exe = Get-ChildItem "$ROOT\dist\vermes-backend\vermes-backend.exe" -ErrorAction SilentlyContinue
if ($exe) { Write-Host "  Backend built: $($exe.Length) bytes" } else { throw "PyInstaller failed" }
Write-Host "  Done"

# Step 8: Electron NSIS build
Write-Host "`nStep 8: Electron NSIS build"
Push-Location "$ROOT\electron"
& "$NODE\npx.cmd" electron-builder --win --x64
Pop-Location
$setup = Get-ChildItem "$ROOT\dist-electron\Vermes Setup*.exe" | Select-Object -First 1
if ($setup) { 
    $mb = [math]::Round($setup.Length / 1MB)
    Write-Host "  NSIS built: $($setup.Name) ($mb MB)"
} else { throw "electron-builder failed" }
Write-Host "  Done"

Write-Host "`n═══════════════════════════════════════════"
Write-Host "  Build Complete!"
Write-Host "  $($setup.FullName)"
Write-Host "═══════════════════════════════════════════"
