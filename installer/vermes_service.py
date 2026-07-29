"""Vermes Gateway — Windows Service wrapper.

Install:  python vermes_service.py install
Start:    python vermes_service.py start
Stop:     python vermes_service.py stop
Remove:   python vermes_service.py remove
"""
import sys
import os
import time
import logging

# Add app directory to path
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# Try to import win32service (pywin32)
try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class VermesGatewayService:
    """Windows Service that runs the Vermes Gateway (uvicorn + FastAPI)."""

    _svc_name_ = "VermesGateway"
    _svc_display_name_ = "Vermes AI Agent Gateway"
    _svc_description_ = "Persistent backend service for Vermes AI Agent desktop app"

    def __init__(self, args=None):
        if HAS_WIN32 and args:
            self.stop_event = win32event.CreateEvent(None, True, False, None)
        else:
            self.stop_event = None
        self.is_running = False

    def SvcStop(self):
        """Called by Windows to stop the service."""
        if HAS_WIN32:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.is_running = False
        if self.stop_event:
            win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        """Called by Windows to start the service."""
        if HAS_WIN32:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ''),
            )
        self.is_running = True
        self._run_gateway()

    def _run_gateway(self):
        """Start uvicorn + FastAPI as the Gateway."""
        # Set environment
        os.environ.setdefault("VERMES_HOME", os.path.expanduser("~/.vermes"))
        os.makedirs(os.environ["VERMES_HOME"], exist_ok=True)

        log_file = os.path.join(os.environ["VERMES_HOME"], "gateway.log")
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        log = logging.getLogger("vermes.gateway")
        log.info("Vermes Gateway service starting...")

        try:
            import uvicorn
            from vermes_cli.web_server import app

            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=9119,
                log_level="info",
            )
            server = uvicorn.Server(config)

            log.info("Gateway listening on port 9119")
            server.run()
        except Exception as e:
            log.error(f"Gateway crashed: {e}", exc_info=True)
            if HAS_WIN32:
                servicemanager.LogErrorMsg(f"Vermes Gateway crashed: {e}")


def run_standalone():
    """Run as a standalone process (not as a Windows service)."""
    svc = VermesGatewayService()
    svc._run_gateway()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "standalone":
        run_standalone()
    elif HAS_WIN32:
        win32serviceutil.HandleCommandLine(VermesGatewayService)
    else:
        # No pywin32 — run standalone
        run_standalone()
