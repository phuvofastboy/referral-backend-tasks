#!/usr/bin/env python3
"""Sinh JWT cho smoke test GraphQL local.

IstioJwtAuthenticator KHÔNG verify chữ ký (Istio mesh lo việc đó upstream),
chỉ decode payload + check claim `iss` khớp issuer (APP_BASE_URI) + map `sub`
tới user qua UserProvider. Vì vậy token tự dựng dùng được cho local/dev test.

Lưu ý: IstioJwtPayloadExtractor dùng base64_decode STANDARD (strict), KHÔNG phải
base64url — nên payload phải encode bằng standard base64 (đúng như script này).

Cách dùng:
    python3 gen_token.py --sub phu_vo@fastboy.net --iss https://localhost \
        --id 1f0e616a-8a04-6c62-8f29-63301b77a039
"""
import argparse
import base64
import json
import time


def b64(d: dict) -> str:
    return base64.b64encode(json.dumps(d, separators=(",", ":")).encode()).decode()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sub", required=True, help="user identifier (email) -> claim sub")
    p.add_argument("--iss", default="https://localhost", help="issuer = APP_BASE_URI")
    p.add_argument("--id", default="", help="user uuid (claim id, optional)")
    p.add_argument("--ttl", type=int, default=86400, help="thời gian sống (giây)")
    args = p.parse_args()

    now = int(time.time())
    header = {"typ": "JWT", "alg": "RS256"}
    payload = {
        "iat": now,
        "exp": now + args.ttl,
        "sub": args.sub,
        "iss": args.iss,
        "aud": "",
        "id": args.id,
        "username": args.sub,
    }
    # signature không được verify -> để chuỗi placeholder
    print(f"{b64(header)}.{b64(payload)}.sig")


if __name__ == "__main__":
    main()
