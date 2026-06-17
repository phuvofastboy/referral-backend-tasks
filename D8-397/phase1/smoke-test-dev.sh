#!/usr/bin/env bash
# Smoke test resell_type trên DEV (qua Hasura gateway).
# Endpoint: https://dev.resell.fastboy.dev/api/graphql
# Auth: JWT RS256 thật (Istio verify chữ ký) — KHÔNG craft tay được như local.
#
# Cách lấy TOKEN: DevTools trên dev.resell.fastboy.dev → copy Authorization Bearer
# của 1 request bất kỳ (vd GetCurrentUser). Token TTL ~1 ngày, hết hạn thì lấy lại.
#
# Prerequisite: remote schema permission SDL (resell_type) đã deploy lên dev
# (commit 62737109) — nếu chưa, Hasura báo "field 'resell_type' not found".

set -u

BASE="https://dev.resell.fastboy.dev/api/graphql"

# >>> DÁN TOKEN MỚI VÀO ĐÂY <<<
TOKEN="eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJpYXQiOjE3ODE2MDQ3ODIsImV4cCI6MTc4MTY5MTE4Miwic3ViIjoicGh1X3ZvQGZhc3Rib3kubmV0IiwiaXNzIjoiaHR0cHM6Ly9kZXYucmVzZWxsLmZhc3Rib3kuZGV2IiwiYXVkIjoiIiwiaWQiOiIxZjBlNjE2YS04YTA0LTZjNjItOGYyOS02MzMwMWI3N2EwMzkiLCJ1c2VybmFtZSI6InBodV92b0BmYXN0Ym95Lm5ldCJ9.wn6Hnm5yikRxJpRr4zxpEPLIqsdLljOEN3H3Y76FgG6mCLI5JEWsuf0EoDJr_OG15W2N8SV0xRK7lxH_CV7-Na6OLgFTfR00QjKHbQ-3E74cxW1Goe3RpiKk49LttoY5gR3U7nldhbH0odzSjht8ZFXsKE4NxZNd3pXO9Bc0zoslH8zPJ7E-4TcY8B1hblidmAPlQkdJ1VbOPLSNQ20LAj5Pebv2nrqQnCL4MGf4_DlWD4QqmACPvhRZ7BPKNbZwA9_dHwwIkNrSxIpjyeeeVOeCuNbSo4kYVxyVv_F24uV40W7gAfuNaArZLvoGwC9AV7kU-Q-ri78jI0uNmHVHrrcs0a80ZRmlGnAHzuPewSa6JPV_YIj7WtPiZVoYU1g_GEj67Z_yHOXjXDv-WOYozR1h3Q3nI3bDE6p6GgGdJ4gAckJMggU4UirS2rdvNnLsYMtD8vu2r0q0aYv6adUoI7OwMHUrBHomQ1NdOurg716zFgkZo_FDE80aBKvA_BEO8J-LIVkyrV027EJ9H01rUWno6bDKkYs_cZUcY6OK5Zzn6IDD7EdIo5alI4Ez1kbgWvyG5-uB8m9XxcphUa46COYfh0dCj7Sv6-gCSg_m3kyhDKK-ur8jTm6KFm0C8jK4hND_P34Xa48Q_psMmGKoMKtY6bGab5K98KyHYFXYTEo"

# Dev data (lấy từ curl mẫu FE). Đổi nếu product/company khác.
PRODUCT_ID="928ec42a-fbc2-449d-bfc9-38296a4701ab"
COMPANY_ID="1f0e6194-7633-6d1c-8199-cbb11ebff8dc"

req() {
  curl -s -X POST "$BASE" \
    -H "authorization: Bearer ${TOKEN}" \
    -H 'x-hasura-role: ROLE_USER' \
    -H 'content-type: application/json' \
    --data-raw "$1"
  echo
}

# Body create order; $1 = đoạn resell_type (vd '"resell_type":"purchase_to_inventory",' hoặc rỗng)
body() {
  cat <<JSON
{"operationName":"CreateOrder","variables":{"input":{${1}"products":[{"product_id":"${PRODUCT_ID}","quantity":1,"referral_order_product_shipping_product":{}}],"shipping_address":{"address":"Ho chi Minh","city":"Ho Chi Minh","country":"VN","name":"test","phone":"+84313123111","postal_code":"70000","state":"Ho Chi Minh"},"status":"draft","is_self_delivery":false,"is_pay_now":false,"create_referral":false,"service_type":"03","company":"${COMPANY_ID}"}},"query":"mutation CreateOrder(\$input: referral_order_create_mutation_input!) { referral_order_create_mutation(input_obj: \$input) { id status is_pay_now internal_id resell_type } }"}
JSON
}

echo "===== 0. Sanity: GetCurrentUser (token còn hạn?) ====="
req '{"operationName":"GetCurrentUser","variables":{"id":"1f0e616a-8a04-6c62-8f29-63301b77a039"},"query":"query GetCurrentUser($id: uuid!) { user_by_pk(id: $id) { email type is_merchant status } }"}'

echo "===== 1. KHÔNG truyền resell_type → kỳ vọng default sell_via_crm ====="
req "$(body '')"

echo "===== 2. resell_type = purchase_to_inventory → kỳ vọng OK ====="
req "$(body '"resell_type":"purchase_to_inventory",')"

echo "===== 3. resell_type = sell_via_crm → kỳ vọng OK ====="
req "$(body '"resell_type":"sell_via_crm",')"

echo "===== 4. resell_type = sell_from_inventory → kỳ vọng REJECT (not a valid choice) ====="
req "$(body '"resell_type":"sell_from_inventory",')"

echo "===== 5. resell_type = invalid_value → kỳ vọng REJECT ====="
req "$(body '"resell_type":"invalid_value",')"
