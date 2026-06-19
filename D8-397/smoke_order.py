#!/usr/bin/env python3
"""Smoke-test referral_order create/update for the qty-based reseller discount.

Crafts a local (unsigned) Istio JWT and calls the local GraphQL endpoint.
Usage:
  python3 smoke_order.py create --resell-type purchase_to_inventory --quantity 3
  python3 smoke_order.py update --id <ORDER_ID> --resell-type purchase_to_inventory --quantity 3
"""
import argparse
import base64
import json
import ssl
import time
import urllib.request

ENDPOINT = "https://localhost/graphql"
PRODUCT_ID = "441cc48e-75ae-4bec-a726-54e9b4a405c8"
COMPANY_ID = "1f11c6b3-162f-6380-ad5a-4da07bc7ebd2"
SUB = "phu_vo@fastboy.net"
USER_ID = "1f0e616a-8a04-6c62-8f29-63301b77a039"
ISS = "https://localhost"


def b64(d):
    return base64.b64encode(json.dumps(d, separators=(",", ":")).encode()).decode()


def make_token():
    now = int(time.time())
    header = b64({"typ": "JWT", "alg": "RS256"})
    payload = b64({
        "iat": now, "exp": now + 86400, "sub": SUB, "iss": ISS,
        "aud": "", "id": USER_ID, "username": SUB,
    })
    return f"{header}.{payload}.sig"


def shipping_address():
    return {
        "address": "Ho Chi Minh", "city": "Ho Chi Minh", "country": "US",
        "name": "Phu Vo Bussiness 2", "phone": "+84932754799",
        "postal_code": "70000", "state": "AK",
    }


def build_input(args):
    products = [{
        "product_id": args.product,
        "quantity": args.quantity,
        "referral_order_product_shipping_product": {},
    }]
    if args.quantity2 is not None:
        products.append({
            "product_id": args.product,
            "quantity": args.quantity2,
            "referral_order_product_shipping_product": {},
        })
    return {
        "products": products,
        "shipping_address": shipping_address(),
        "status": args.status,
        "resell_type": args.resell_type,
        "is_self_delivery": False,
        "is_pay_now": False,
        "create_referral": False,
        "company": args.company,
    }


def post(body):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {make_token()}",
            "x-hasura-role": "ROLE_USER",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, context=ctx) as r:
        return json.loads(r.read().decode())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("op", choices=["create", "update"])
    p.add_argument("--id", help="order id (update)")
    p.add_argument("--resell-type", default="purchase_to_inventory")
    p.add_argument("--quantity", type=int, default=3)
    p.add_argument("--quantity2", type=int, default=None, help="second line for same product (tests qty aggregation)")
    p.add_argument("--status", default="draft")
    p.add_argument("--product", default=PRODUCT_ID)
    p.add_argument("--company", default=COMPANY_ID)
    args = p.parse_args()

    inp = build_input(args)

    if args.op == "create":
        body = {
            "operationName": "CreateOrder",
            "variables": {"input": inp},
            "query": (
                "mutation CreateOrder($input: referral_order_create_mutation_input!) {"
                "  referral_order_create_mutation(input_obj: $input) {"
                "    id internal_id status resell_type card_type } }"
            ),
        }
    else:
        body = {
            "operationName": "UpdateOrder",
            "variables": {"id": args.id, "input": inp},
            "query": (
                "mutation UpdateOrder($id: ID!, $input: referral_order_update_mutation_input!) {"
                "  referral_order_update_mutation(id: $id, input_obj: $input) {"
                "    id internal_id status resell_type card_type } }"
            ),
        }

    print(json.dumps(post(body), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
