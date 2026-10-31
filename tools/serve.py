"""Single-process server with bounded video WebSocket buffers."""

import argparse

import uvicorn
from security_app.video import VIDEO_MESSAGE_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(
        "security_app.main:app",
        host=args.host,
        port=args.port,
        workers=1,
        ws="websockets",
        ws_max_size=VIDEO_MESSAGE_BYTES,
        ws_max_queue=2,
        ws_per_message_deflate=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
