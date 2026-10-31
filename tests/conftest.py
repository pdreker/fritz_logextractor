import signal

import pytest


@pytest.fixture(autouse=True)
def _keep_signal_handlers():
    """main() installs handlers for SIGTERM and SIGINT; give pytest its own back."""
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
