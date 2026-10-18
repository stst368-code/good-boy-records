"""Platform-native event loop for the single-process review HTTP server."""
import asyncio
import logging
import sys


def _windows_exception_handler(loop, context):
    """Handle only Windows' reset during Proactor connection cleanup."""
    error = context.get("exception")
    callback = getattr(context.get("handle"), "_callback", None)
    if (
        isinstance(error, ConnectionResetError)
        and getattr(error, "winerror", None) == 10054
        and getattr(callback, "__module__", None) == "asyncio.proactor_events"
        and getattr(callback, "__name__", None) == "_call_connection_lost"
    ):
        logging.getLogger(__name__).debug(
            "Peer reset a connection during Proactor cleanup (WinError 10054)."
        )
        return
    loop.default_exception_handler(context)


def create_loop():
    # Forcing Selector on Windows exposed repeated empty-buffer writes.
    # Use native IOCP transports instead; never suppress that assertion.
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        loop.set_exception_handler(_windows_exception_handler)
        return loop
    return asyncio.SelectorEventLoop()
