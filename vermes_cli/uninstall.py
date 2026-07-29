"""
Vermes Uninstaller.

Provides options for:
- Full uninstall: Remove everything including configs and data
- Keep data: Remove code but keep ~/.vermes/ (configs, sessions, logs)
"""

import logging

logger = logging.getLogger(__name__)
import os
import shutil
import subprocess
from pathlib import Path

from vermes_constants import get_vermes_home

from vermes_cli.colors import Colors, color

def log_info(msg: str):
    logger.info(f"{color('→', Colors.CYAN)} {msg}")

def log_success(msg: str):
    logger.info(f"{color('✓', Colors.GREEN)} {msg}")

def log_warn(msg: str):
    logger.info(f"{color('⚠', Colors.YELLOW)} {msg}")

def get_project_root() -> Path:
    """Get the project installation directory."""
    return Path(__file__).parent.parent.resolve()


def find_shell_configs() -> list:
    """Find shell configuration files that might have PATH entries."""
    home = Path.home()
    configs = []
    
    candidates = [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
        home / ".zshrc",
        home / ".zprofile",
    ]
    
    for config in candidates:
        if config.exists():
            configs.append(config)
    
    return configs


def remove_path_from_shell_configs():
    """Remove Vermes PATH entries from shell configuration files."""
    configs = find_shell_configs()
    removed_from = []
    
    for config_path in configs:
        try:
            content = config_path.read_text()
            original_content = content
            
            # Remove lines containing Vermes-agent or Vermes PATH entries
            new_lines = []
            skip_next = False
            
            for line in content.split('\n'):
                # Skip the "# Vermes" comment and following line
                if '# Vermes' in line or '# Vermes-agent' in line:
                    skip_next = True
                    continue
                if skip_next and ('Vermes' in line.lower() and 'PATH' in line):
                    skip_next = False
                    continue
                skip_next = False
                
                # Remove any PATH line containing Vermes
                if 'Vermes' in line.lower() and ('PATH=' in line or 'path=' in line.lower()):
                    continue
                    
                new_lines.append(line)
            
            new_content = '\n'.join(new_lines)
            
            # Clean up multiple blank lines
            while '\n\n\n' in new_content:
                new_content = new_content.replace('\n\n\n', '\n\n')
            
            if new_content != original_content:
                config_path.write_text(new_content)
                removed_from.append(config_path)
                
        except Exception as e:
            log_warn(f"Could not update {config_path}: {e}")
    
    return removed_from


def remove_wrapper_script():
    """Remove the Vermes wrapper script if it exists."""
    wrapper_paths = [
        Path.home() / ".local" / "bin" / "Vermes",
        Path("/usr/local/bin/Vermes"),
    ]
    
    removed = []
    for wrapper in wrapper_paths:
        if wrapper.exists():
            try:
                # Check if it's our wrapper (contains vermes_cli reference)
                content = wrapper.read_text()
                if 'vermes_cli' in content or 'Vermes-agent' in content:
                    wrapper.unlink()
                    removed.append(wrapper)
            except Exception as e:
                log_warn(f"Could not remove {wrapper}: {e}")
    
    return removed


def uninstall_gateway_service():
    """Stop and uninstall the gateway service (systemd, launchd, Windows
    Scheduled Task / Startup folder) and kill any standalone gateway processes.

    Delegates to the gateway module which handles:
    - Linux: user + system systemd services (with proper DBUS env setup)
    - macOS: launchd plists
    - Windows: Scheduled Task + Startup-folder fallback, via ``gateway_windows``
    - All platforms: standalone ``vermes gateway run`` processes
    - Termux/Android: skips systemd (no systemd on Android), still kills standalone processes
    """
    import platform
    stopped_something = False

    # 1. Kill any standalone gateway processes (all platforms, including Termux)
    try:
        from vermes_cli.gateway import kill_gateway_processes, find_gateway_pids
        pids = find_gateway_pids()
        if pids:
            killed = kill_gateway_processes()
            if killed:
                log_success(f"Killed {killed} running gateway process(es)")
                stopped_something = True
    except Exception as e:
        log_warn(f"Could not check for gateway processes: {e}")

    system = platform.system()

    # Termux/Android has no systemd and no launchd — nothing left to do.
    prefix = os.getenv("PREFIX", "")
    is_termux = bool(os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix)
    if is_termux:
        return stopped_something

    # 2. Linux: uninstall systemd services (both user and system scopes)
    if system == "Linux":
        try:
            from vermes_cli.gateway import (
                get_systemd_unit_path,
                get_service_name,
                _systemctl_cmd,
            )
            svc_name = get_service_name()

            for is_system in (False, True):
                unit_path = get_systemd_unit_path(system=is_system)
                if not unit_path.exists():
                    continue

                scope = "system" if is_system else "user"
                try:
                    if is_system and os.geteuid() != 0:  # windows-footgun: ok — Linux systemd uninstall path, guarded by `if system == "Linux"` above
                        log_warn(f"System gateway service exists at {unit_path} "
                                 f"but needs sudo to remove")
                        continue

                    cmd = _systemctl_cmd(is_system)
                    subprocess.run(cmd + ["stop", svc_name],
                                   capture_output=True, check=False)
                    subprocess.run(cmd + ["disable", svc_name],
                                   capture_output=True, check=False)
                    unit_path.unlink()
                    subprocess.run(cmd + ["daemon-reload"],
                                   capture_output=True, check=False)
                    log_success(f"Removed {scope} gateway service ({unit_path})")
                    stopped_something = True
                except Exception as e:
                    log_warn(f"Could not remove {scope} gateway service: {e}")
        except Exception as e:
            log_warn(f"Could not check systemd gateway services: {e}")

    # 3. macOS: uninstall launchd plist
    elif system == "Darwin":
        try:
            from vermes_cli.gateway import get_launchd_plist_path
            plist_path = get_launchd_plist_path()
            if plist_path.exists():
                subprocess.run(["launchctl", "unload", str(plist_path)],
                               capture_output=True, check=False)
                plist_path.unlink()
                log_success(f"Removed macOS gateway service ({plist_path})")
                stopped_something = True
        except Exception as e:
            log_warn(f"Could not remove launchd gateway service: {e}")

    # 4. Windows: uninstall Scheduled Task + Startup-folder entry.  The
    #    gateway_windows module already knows how to locate and remove both
    #    code paths (schtasks /Delete + .cmd unlink) and how to stop any
    #    running detached pythonw gateway process.  We call into it so the
    #    uninstall logic stays in exactly one place.
    elif system == "Windows":
        try:
            from vermes_cli import gateway_windows
            if gateway_windows.is_installed() or gateway_windows.is_task_registered() \
                    or gateway_windows.is_startup_entry_installed():
                try:
                    gateway_windows.stop()
                except Exception as e:
                    log_warn(f"Could not stop Windows gateway cleanly: {e}")
                try:
                    gateway_windows.uninstall()
                    log_success("Removed Windows gateway (Scheduled Task + Startup entry)")
                    stopped_something = True
                except Exception as e:
                    log_warn(f"Could not fully uninstall Windows gateway: {e}")
        except Exception as e:
            log_warn(f"Could not check Windows gateway service: {e}")

    return stopped_something


# ============================================================================
# Windows-specific uninstall helpers
# ============================================================================
#
# The installer (``scripts/install.ps1``) does four Windows-only things that
# ``remove_path_from_shell_configs`` / ``remove_wrapper_script`` don't cover:
#
#   1. Sets User-scope env vars ``VERMES_HOME`` and ``VERMES_GIT_BASH_PATH``
#      via ``[Environment]::SetEnvironmentVariable(..., "User")``.  These
#      don't live in ~/.bashrc — they're in the Windows registry at
#      HKCU\Environment.
#   2. Prepends to User-scope ``PATH`` (same registry location) entries
#      like ``%LOCALAPPDATA%\Vermes\git\cmd``, ``%LOCALAPPDATA%\Vermes\git\bin``,
#      ``%LOCALAPPDATA%\Vermes\git\usr\bin``, ``%LOCALAPPDATA%\Vermes\node``.
#      Again not in any rc file — only accessible via the registry or the
#      .NET [Environment] API.
#   3. Downloads PortableGit to ``%LOCALAPPDATA%\Vermes\git\`` and Node to
#      ``%LOCALAPPDATA%\Vermes\node\`` as user-scoped, isolated copies.
#      These are ~200MB combined and serve no purpose after uninstall.
#   4. On the ``Vermes dashboard`` + gateway paths, drops files into
#      ``%LOCALAPPDATA%\Vermes\gateway-service\`` and sometimes
#      ``%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`` — the
#      latter is handled by ``gateway_windows.uninstall()`` already.
#
# Running a PowerShell one-liner per operation is overkill and fragile on
# locked-down machines (Constrained Language Mode, restricted ExecutionPolicy).
# Direct registry writes via ``winreg`` work without spawning any subprocess
# and apply immediately for new shells (SendMessage WM_SETTINGCHANGE would
# be nicer but requires ctypes and buys us nothing — the user will log out
# or open a new terminal anyway).


def _vermes_path_markers(VERMES_home: Path) -> list[str]:
    """Path-entry substrings that identify Vermes-owned User-PATH entries."""
    root = str(VERMES_home).rstrip("\\/")
    # Match on prefix so sub-entries (git\cmd, git\bin, git\usr\bin, node, etc.)
    # all get swept.  Also match the bare Vermes-agent install dir.
    markers = [root + "\\Vermes-agent", root + "\\git", root + "\\node", root + "\\venv"]
    # Also match if VERMES_HOME was customised to somewhere else — find-and-nuke
    # any entry whose path component contains "Vermes".  We don't want to catch
    # unrelated entries like "cvermes-foo" or "ephermeral", so we look for
    # backslash-Vermes as a word-ish boundary.
    return markers


def remove_path_from_windows_registry(VERMES_home: Path) -> list[str]:
    """Strip Vermes-owned entries from User-scope PATH in the registry.

    Returns the list of removed path entries.  Operates on HKCU\\Environment,
    same key the installer wrote to via ``[Environment]::SetEnvironmentVariable``.
    """
    try:
        import winreg
    except ImportError:
        return []  # not on Windows, nothing to do

    removed: list[str] = []
    key_path = "Environment"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                path_value, path_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return []
            # Preserve REG_EXPAND_SZ vs REG_SZ so unexpanded %VARS% survive.
            entries = [e for e in path_value.split(";") if e]
            markers = _vermes_path_markers(VERMES_home)
            kept: list[str] = []
            for entry in entries:
                entry_norm = entry.rstrip("\\/")
                matched = any(entry_norm.lower().startswith(m.lower()) for m in markers)
                if matched:
                    removed.append(entry)
                else:
                    kept.append(entry)
            if removed:
                new_value = ";".join(kept)
                winreg.SetValueEx(key, "Path", 0, path_type, new_value)
    except OSError as e:
        log_warn(f"Could not edit User PATH in registry: {e}")
    return removed


def remove_vermes_env_vars_windows() -> list[str]:
    """Delete VERMES_HOME and VERMES_GIT_BASH_PATH from User-scope env vars."""
    try:
        import winreg
    except ImportError:
        return []

    removed: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            for name in ("VERMES_HOME", "VERMES_GIT_BASH_PATH"):
                try:
                    winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                try:
                    winreg.DeleteValue(key, name)
                    removed.append(name)
                except OSError as e:
                    log_warn(f"Could not delete {name} from User env: {e}")
    except OSError as e:
        log_warn(f"Could not open User Environment key: {e}")
    return removed


def remove_portable_tooling_windows(VERMES_home: Path) -> list[Path]:
    """Delete PortableGit and Node installs the Windows installer created under
    ``%LOCALAPPDATA%\\Vermes\\``.  Only called on full uninstall; they're
    isolated from any system Git / Node so they cannot break other tools."""
    removed: list[Path] = []
    for sub in ("git", "node", "gateway-service"):
        target = VERMES_home / sub
        if target.exists():
            try:
                shutil.rmtree(target, ignore_errors=False)
                removed.append(target)
            except Exception as e:
                log_warn(f"Could not remove {target}: {e}")
    return removed


def _is_windows() -> bool:
    import sys
    return sys.platform == "win32"


def _is_default_vermes_home(VERMES_home: Path) -> bool:
    """Return True when ``VERMES_home`` points at the default (non-profile) root."""
    try:
        from vermes_constants import get_default_vermes_root
        return VERMES_home.resolve() == get_default_vermes_root().resolve()
    except Exception:
        return False


def _discover_named_profiles():
    """Return a list of ``ProfileInfo`` for every non-default profile, or ``[]``
    if profile support is unavailable or nothing is installed beyond the
    default root."""
    try:
        from vermes_cli.profiles import list_profiles
    except Exception:
        return []
    try:
        return [p for p in list_profiles() if not getattr(p, "is_default", False)]
    except Exception as e:
        log_warn(f"Could not enumerate profiles: {e}")
        return []


def _uninstall_profile(profile) -> None:
    """Fully uninstall a single named profile: stop its gateway service,
    remove its alias wrapper, and wipe its VERMES_HOME directory.

    We shell out to ``Vermes -p <name> gateway stop|uninstall`` because
    service names, unit paths, and plist paths are all derived from the
    current VERMES_HOME and can't be easily switched in-process.
    """
    import sys as _sys


    name = profile.name
    profile_home = profile.path

    log_info(f"Uninstalling profile '{name}'...")

    # 1. Stop and remove this profile's gateway service.
    #    Use `python -m vermes_cli.main` so we don't depend on a `Vermes`
    #    wrapper that may be half-removed mid-uninstall.
    VERMES_invocation = [_sys.executable, "-m", "vermes_cli.main", "--profile", name]
    for subcmd in ("stop", "uninstall"):
        try:
            subprocess.run(
                VERMES_invocation + ["gateway", subcmd],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log_warn(f"  Gateway {subcmd} timed out for '{name}'")
        except Exception as e:
            log_warn(f"  Could not run gateway {subcmd} for '{name}': {e}")

    # 2. Remove the wrapper alias script at ~/.local/bin/<name> (if any).
    alias_path = getattr(profile, "alias_path", None)
    if alias_path and alias_path.exists():
        try:
            alias_path.unlink()
            log_success(f"  Removed alias {alias_path}")
        except Exception as e:
            log_warn(f"  Could not remove alias {alias_path}: {e}")

    # 3. Wipe the profile's VERMES_HOME directory.
    try:
        if profile_home.exists():
            shutil.rmtree(profile_home)
            log_success(f"  Removed {profile_home}")
    except Exception as e:
        log_warn(f"  Could not remove {profile_home}: {e}")


def run_uninstall(args):
    """
    Run the uninstall process.
    
    Options:
    - Full uninstall: removes code + ~/.vermes/ (configs, data, logs)
    - Keep data: removes code but keeps ~/.vermes/ for future reinstall
    """
    project_root = get_project_root()
    VERMES_home = get_vermes_home()

    # Detect named profiles when uninstalling from the default root —
    # offer to clean them up too instead of leaving zombie VERMES_HOMEs
    # and systemd units behind.
    is_default_profile = _is_default_vermes_home(VERMES_home)
    named_profiles = _discover_named_profiles() if is_default_profile else []

    logger.info()
    logger.info(color("┌─────────────────────────────────────────────────────────┐", Colors.MAGENTA, Colors.BOLD))
    logger.info(color("│            ⚕ Vermes Uninstaller                  │", Colors.MAGENTA, Colors.BOLD))
    logger.info(color("└─────────────────────────────────────────────────────────┘", Colors.MAGENTA, Colors.BOLD))
    logger.info()
    
    # Show what will be affected
    logger.info(color("Current Installation:", Colors.CYAN, Colors.BOLD))
    logger.info(f"  Code:    {project_root}")
    logger.info(f"  Config:  {VERMES_home / 'config.yaml'}")
    logger.info(f"  Secrets: {VERMES_home / '.env'}")
    logger.info(f"  Data:    {VERMES_home / 'cron/'}, {VERMES_home / 'sessions/'}, {VERMES_home / 'logs/'}")
    logger.info()

    if named_profiles:
        logger.info(color("Other profiles detected:", Colors.CYAN, Colors.BOLD))
        for p in named_profiles:
            running = " (gateway running)" if getattr(p, "gateway_running", False) else ""
            logger.info(f"  • {p.name}{running}: {p.path}")
        logger.info()
    
    # Ask for confirmation
    logger.info(color("Uninstall Options:", Colors.YELLOW, Colors.BOLD))
    logger.info()
    logger.info("  1) " + color("Keep data", Colors.GREEN) + " - Remove code only, keep configs/sessions/logs")
    logger.info("     (Recommended - you can reinstall later with your settings intact)")
    logger.info()
    logger.info("  2) " + color("Full uninstall", Colors.RED) + " - Remove everything including all data")
    logger.info("     (Warning: This deletes all configs, sessions, and logs permanently)")
    logger.info()
    logger.info("  3) " + color("Cancel", Colors.CYAN) + " - Don't uninstall")
    logger.info()
    
    try:
        choice = input(color("Select option [1/2/3]: ", Colors.BOLD)).strip()
    except (KeyboardInterrupt, EOFError):
        logger.info()
        logger.info("Cancelled.")
        return
    
    if choice == "3" or choice.lower() in {"c", "cancel", "q", "quit", "n", "no"}:
        logger.info()
        logger.info("Uninstall cancelled.")
        return
    
    full_uninstall = (choice == "2")

    # When doing a full uninstall from the default profile, also offer to
    # remove any named profiles — stopping their gateway services, unlinking
    # their alias wrappers, and wiping their VERMES_HOME dirs. Otherwise
    # those leave zombie services and data behind.
    remove_profiles = False
    if full_uninstall and named_profiles:
        logger.info()
        logger.info(color("Other profiles will NOT be removed by default.", Colors.YELLOW))
        logger.info(f"Found {len(named_profiles)} named profile(s): " +
              ", ".join(p.name for p in named_profiles))
        logger.info()
        try:
            resp = input(color(
                f"Also stop and remove these {len(named_profiles)} profile(s)? [y/N]: ",
                Colors.BOLD
            )).strip().lower()
        except (KeyboardInterrupt, EOFError):
            logger.info()
            logger.info("Cancelled.")
            return
        remove_profiles = resp in {"y", "yes"}

    # Final confirmation
    logger.info()
    if full_uninstall:
        logger.info(color("⚠️  WARNING: This will permanently delete ALL Vermes data!", Colors.RED, Colors.BOLD))
        logger.info(color("   Including: configs, API keys, sessions, scheduled jobs, logs", Colors.RED))
        if remove_profiles:
            logger.info(color(
                f"   Plus {len(named_profiles)} profile(s): " +
                ", ".join(p.name for p in named_profiles),
                Colors.RED
            ))
    else:
        logger.info("This will remove the Vermes code but keep your configuration and data.")
    
    logger.info()
    try:
        confirm = input(f"Type '{color('yes', Colors.YELLOW)}' to confirm: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        logger.info()
        logger.info("Cancelled.")
        return
    
    if confirm != "yes":
        logger.info()
        logger.info("Uninstall cancelled.")
        return
    
    logger.info()
    logger.info(color("Uninstalling...", Colors.CYAN, Colors.BOLD))
    logger.info()
    
    # 1. Stop and uninstall gateway service + kill standalone processes
    log_info("Checking for running gateway...")
    if not uninstall_gateway_service():
        log_info("No gateway service or processes found")
    
    # 2. Remove PATH entries from shell configs (POSIX) AND from the Windows
    #    User-scope registry.  Both helpers no-op on the wrong platform so we
    #    can safely call them unconditionally.
    log_info("Removing PATH entries from shell configs...")
    removed_configs = remove_path_from_shell_configs()
    if removed_configs:
        for config in removed_configs:
            log_success(f"Updated {config}")
    else:
        log_info("No PATH entries found to remove in shell rc files")

    if _is_windows():
        log_info("Removing PATH entries from Windows User environment...")
        # Expand %LOCALAPPDATA% etc. in VERMES_home so the marker matching is
        # against fully resolved paths — installer writes literal strings
        # like C:\Users\<u>\AppData\Local\Vermes\git\cmd, not %LOCALAPPDATA%.
        removed_path_entries = remove_path_from_windows_registry(Path(os.path.expandvars(str(VERMES_home))))
        if removed_path_entries:
            for entry in removed_path_entries:
                log_success(f"Removed from User PATH: {entry}")
        else:
            log_info("No Vermes-owned PATH entries in User environment")

        log_info("Removing VERMES_HOME / VERMES_GIT_BASH_PATH User env vars...")
        removed_env = remove_vermes_env_vars_windows()
        if removed_env:
            for name in removed_env:
                log_success(f"Removed User env var: {name}")
        else:
            log_info("No Vermes-set User env vars to remove")
    
    # 3. Remove wrapper script
    log_info("Removing Vermes command...")
    removed_wrappers = remove_wrapper_script()
    if removed_wrappers:
        for wrapper in removed_wrappers:
            log_success(f"Removed {wrapper}")
    else:
        log_info("No wrapper script found")
    
    # 4. Remove installation directory (code)
    log_info("Removing installation directory...")
    
    # Check if we're running from within the install dir
    # We need to be careful here
    try:
        if project_root.exists():
            # If the install is inside ~/.vermes/, just remove the Vermes-agent subdir
            if VERMES_home in project_root.parents or project_root.parent == VERMES_home:
                shutil.rmtree(project_root)
                log_success(f"Removed {project_root}")
            else:
                # Installation is somewhere else entirely
                shutil.rmtree(project_root)
                log_success(f"Removed {project_root}")
    except Exception as e:
        log_warn(f"Could not fully remove {project_root}: {e}")
        log_info("You may need to manually remove it")

    # 4b. Remove Windows-only installer artifacts that are NOT user data:
    #     PortableGit, bundled Node, gateway-service dir.  Installer put them
    #     under VERMES_HOME but they're install tooling, not config — safe to
    #     remove even in "keep data" mode.  If we're doing a full uninstall
    #     the step-5 rmtree(VERMES_home) would sweep them anyway; calling
    #     this helper there is a no-op since they'll already be gone.
    if _is_windows():
        log_info("Removing Windows installer artifacts (PortableGit, Node, gateway-service)...")
        removed_artifacts = remove_portable_tooling_windows(VERMES_home)
        if removed_artifacts:
            for path in removed_artifacts:
                log_success(f"Removed {path}")
        else:
            log_info("No Windows installer artifacts to remove")
    
    # 5. Optionally remove ~/.vermes/ data directory (and named profiles)
    if full_uninstall:
        # 5a. Stop and remove each named profile's gateway service and
        #     alias wrapper. The profile VERMES_HOME dirs live under
        #     ``<default>/profiles/<name>/`` and will be swept away by the
        #     rmtree below, but services + alias scripts live OUTSIDE the
        #     default root and have to be cleaned up explicitly.
        if remove_profiles and named_profiles:
            for prof in named_profiles:
                _uninstall_profile(prof)

        log_info("Removing configuration and data...")
        try:
            if VERMES_home.exists():
                shutil.rmtree(VERMES_home)
                log_success(f"Removed {VERMES_home}")
        except Exception as e:
            log_warn(f"Could not fully remove {VERMES_home}: {e}")
            log_info("You may need to manually remove it")
    else:
        log_info(f"Keeping configuration and data in {VERMES_home}")
    
    # Done
    logger.info()
    logger.info(color("┌─────────────────────────────────────────────────────────┐", Colors.GREEN, Colors.BOLD))
    logger.info(color("│              ✓ Uninstall Complete!                      │", Colors.GREEN, Colors.BOLD))
    logger.info(color("└─────────────────────────────────────────────────────────┘", Colors.GREEN, Colors.BOLD))
    logger.info()
    
    if not full_uninstall:
        logger.info(color("Your configuration and data have been preserved:", Colors.CYAN))
        logger.info(f"  {VERMES_home}/")
        logger.info()
        logger.info("To reinstall later with your existing settings:")
        if _is_windows():
            logger.info(color("  iex (irm https://raw.githubusercontent.com/donghzs/vermes/main/scripts/install.ps1)", Colors.DIM))
        else:
            logger.info(color("  curl -fsSL https://raw.githubusercontent.com/donghzs/vermes/main/scripts/install.sh | bash", Colors.DIM))
        logger.info()

    if _is_windows():
        logger.info(color("Open a new terminal (PowerShell / Windows Terminal) to pick up", Colors.YELLOW))
        logger.info(color("the updated User PATH and environment variables.", Colors.YELLOW))
    else:
        logger.info(color("Reload your shell to complete the process:", Colors.YELLOW))
        logger.info("  source ~/.bashrc  # or ~/.zshrc")
    logger.info()
    logger.info("Thank you for using Vermes! ⚕")
    logger.info()
