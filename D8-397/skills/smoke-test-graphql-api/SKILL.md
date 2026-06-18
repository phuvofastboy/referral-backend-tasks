---
name: smoke-test-graphql-api
description: >
  Hướng dẫn step-by-step tạo JWT token và smoke test một GraphQL mutation/query
  của referral-backend bằng curl trên môi trường local (https://localhost/graphql).
  Dùng khi cần verify nhanh một resolver mới (vd thêm field vào mutation) hoạt động
  end-to-end: schema nhận field → resolver → service → persist DB, mà không cần FE
  hay JWT thật từ Istio.
---

# Smoke test GraphQL API bằng curl (local)

Quy trình kiểm thử nhanh một GraphQL endpoint trên local mà không cần đăng nhập qua
Istio. Áp dụng cho mọi mutation/query có `#[Roles('ROLE_USER')]` + `#[GraphQL\InjectUser]`.

## Vì sao tự dựng được JWT

`App\Security\Istio\IstioJwtAuthenticator` **không verify chữ ký** — Istio mesh đã
verify upstream rồi forward token. Authenticator local chỉ:

1. Decode phần payload của JWT (`base64_decode` **standard/strict**, không phải base64url).
2. Check claim `iss` khớp issuer = `APP_BASE_URI` (xem `.env`, local = `https://localhost`).
3. Map claim `sub` → user qua `UserProvider::loadUserByIdentifier()` (theo email).

=> Chỉ cần token có `iss` đúng + `sub` là email user có thật trong DB. Chữ ký để placeholder.
`exp` không bị check nhưng nên set tương lai cho giống thật.

> ⚠️ Đây KHÔNG phải lỗ hổng: ngoài service mesh không ai gọi thẳng tới Symfony được.
> Cách này chỉ phục vụ test local/dev.

## Các bước

### Bước 1 — Lấy issuer local

```bash
grep APP_BASE_URI app/.env          # vd: APP_BASE_URI=https://localhost
```

`iss` của token phải khớp chính xác giá trị này.

### Bước 2 — Tìm user + dữ liệu test trong DB

User identifier là **email** (cột `email` của bảng `user`). Lấy 1 user + 1 bản ghi
phù hợp để thao tác (vd order draft do chính user đó tạo):

```bash
# user
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
  "SELECT email, id, type FROM \"user\" WHERE email='phu_vo@fastboy.net';"

# order draft + chủ sở hữu + company (ví dụ cho ReferralOrder)
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
  "SELECT ro.id, ro.internal_id, ro.status, ro.resell_type, u.email, ro.company_id
   FROM referral_order ro JOIN \"user\" u ON u.id = ro.created_by_id
   WHERE ro.status='draft' ORDER BY ro.internal_id DESC LIMIT 5;"
```

Mutation update/create thường cần thêm `products`, `shipping_address`… → lấy luôn từ
bản ghi sẵn có để request sát thực tế (giảm rủi ro fail do CRM validate):

```bash
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
  "SELECT product_id, quantity, is_shipping_product FROM referral_order_product
   WHERE referral_order_id='<ORDER_ID>';"
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
  "SELECT shipping_address, is_self_delivery, is_pay_now FROM referral_order WHERE id='<ORDER_ID>';"
```

### Bước 3 — Clear cache để rebuild GraphQL schema

Bắt buộc sau khi sửa Input/Resolver/EntityType (GraphQLite cache schema):

```bash
docker compose exec -T apache php bin/console cache:clear
```

### Bước 4 — Sinh JWT

Dùng helper kèm trong skill (`gen_token.py`):

```bash
TOKEN=$(python3 tasks/D8-397/skills/smoke-test-graphql-api/gen_token.py \
  --sub phu_vo@fastboy.net \
  --iss https://localhost \
  --id 1f0e616a-8a04-6c62-8f29-63301b77a039)
echo "$TOKEN"
```

Inline (không cần file) cũng được:

```bash
TOKEN=$(python3 - <<'PY'
import base64, json, time
b64=lambda d: base64.b64encode(json.dumps(d,separators=(',',':')).encode()).decode()
now=int(time.time())
print(f'{b64({"typ":"JWT","alg":"RS256"})}.'
      f'{b64({"iat":now,"exp":now+86400,"sub":"phu_vo@fastboy.net","iss":"https://localhost","aud":"","id":"1f0e616a-8a04-6c62-8f29-63301b77a039","username":"phu_vo@fastboy.net"})}.sig')
PY
)
```

### Bước 5 — (tuỳ chọn) Introspect schema xác nhận field tồn tại

Introspection không cần auth — xác nhận field mới đã vào schema trước khi test thực thi:

```bash
curl -sk -X POST https://localhost/graphql \
  -H 'Content-Type: application/json' \
  -d '{"query":"query{__type(name:\"referral_order_update_mutation_input\"){inputFields{name type{kind name ofType{name}}}}}"}' \
  | python3 -m json.tool
```

### Bước 6 — Gọi mutation/query thật

- Endpoint local: `https://localhost/graphql` (self-signed cert → `curl -k`).
- Header: `authorization: Bearer $TOKEN` + `x-hasura-role: ROLE_USER`.
- Đưa field cần verify (vd `resell_type`) vào cả `variables.input` lẫn selection set.

```bash
curl -sk -X POST 'https://localhost/graphql' \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $TOKEN" \
  -H 'x-hasura-role: ROLE_USER' \
  --data-raw '{"operationName":"UpdateOrder","variables":{"id":"<ORDER_ID>","input":{"products":[{"product_id":"<PID>","quantity":1,"referral_order_product_shipping_product":{}}],"shipping_address":{"address":"Ho chi Minh","city":"Ho Chi Minh","country":"VN","name":"test","phone":"+84313123111","postal_code":"70000","state":"Ho Chi Minh"},"status":"draft","resell_type":"purchase_to_inventory","is_self_delivery":false,"is_pay_now":true,"create_referral":false,"company":"<COMPANY_ID>"}},"query":"mutation UpdateOrder($id: ID!, $input: referral_order_update_mutation_input!) {\n  referral_order_update_mutation(id: $id, input_obj: $input) {\n    id\n    internal_id\n    status\n    resell_type\n  }\n}\n"}' \
  | python3 -m json.tool
```

### Bước 7 — Verify đã persist trong DB (không chỉ echo)

```bash
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
  "SELECT internal_id, status, resell_type FROM referral_order WHERE id='<ORDER_ID>';"
```

### Bước 8 — Test edge case partial-update (nếu field cho phép null)

Gọi lại mutation **bỏ** field đó, kỳ vọng DB giữ nguyên giá trị cũ (với field set theo
pattern `if ($x !== null)`), rồi query DB xác nhận không bị ghi đè.

## Checklist khi smoke test 1 field mới

- [ ] `cache:clear` sau khi sửa Input/Resolver/EntityType.
- [ ] Introspect input type: field xuất hiện.
- [ ] Mutation trả đúng giá trị field trong response.
- [ ] DB persist đúng giá trị.
- [ ] (nếu nullable) bỏ field → giữ nguyên giá trị cũ.

## Lỗi thường gặp

| Triệu chứng | Nguyên nhân |
| --- | --- |
| `Istio JWT in request's missing or invalid.` (401) | `iss` không khớp `APP_BASE_URI`, hoặc payload encode bằng base64url thay vì standard, hoặc thiếu prefix `Bearer ` |
| `loadUserByIdentifier` không tìm thấy user | `sub` không phải email user có thật trong DB đang chạy |
| Field mới không có trong schema | quên `cache:clear` sau khi sửa code |
| Mutation fail ở bước CRM (shipping fee / product) | dùng product_id/company không tồn tại — lấy dữ liệu từ bản ghi sẵn có (Bước 2) |
| `could not parse ... Array` khi introspect không biến | client serialize `[]` thành JSON array; truyền biến qua `$name: String!` để thành object |
