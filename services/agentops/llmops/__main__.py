"""Start the product on 127.0.0.1 (loopback only), or on 0.0.0.0 inside the VPS container (LLMOPS_HOST, EV-VPS-01 CHG-V01)."""
import uvicorn

from .app import create_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    # loop="asyncio": the standard loop uses Python sockets that netguard guards (no uvloop C sockets)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning", loop="asyncio", http="h11")


if __name__ == "__main__":
    main()
