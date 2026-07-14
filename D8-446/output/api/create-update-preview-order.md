# API: Create / Update / Preview Order — cập nhật chọn serials (D8-446 task 07)

Doc này mô tả **phần thay đổi sau khi implement task 07** cho 3 mutation order. Trọng tâm là
việc cho phép chọn serial tồn kho (`reseller_inventory_product_item`) trên từng product line, và
các luật validate kèm theo. Các field/luật khác của order giữ nguyên như trước.

| Mutation | Arg | Input type | Output |
|---|---|---|---|
| `referral_order_create_mutation` | `input_obj` | `referral_order_create_mutation_input!` | `referral_order_entity_type` |
| `referral_order_update_mutation` | `id: ID!`, `input_obj` | `referral_order_update_mutation_input!` | `referral_order_entity_type` |
| `referral_order_preview_order` | `input_obj` | `preview_referral_order_input!` | `referral_order_entity_type` |

- **Endpoint**: `POST https://localhost/graphql` (dev, self-signed → `curl -k`).
- **Auth**: `authorization: Bearer <JWT>` + `x-hasura-role: ROLE_USER`.
- **Scope**: chỉ áp dụng cho order `resell_type = "sell_from_inventory"`. Các resell_type khác bỏ
  qua toàn bộ logic serial (field `serials` nếu truyền sẽ bị ignore).

---

## 1. Thay đổi Input

### `referral_order_product_input` — thêm field `serials`

Mỗi product line (non-shipping) có thể kèm danh sách serial được chọn từ kho:

```graphql
input referral_order_product_input {
  product_id: String
  quantity: Int
  # ... các field cũ (unit_price, sale_price, is_shipping_product, ...) giữ nguyên
  serials: [referral_order_product_serial_input!]   # MỚI
}
```

### `referral_order_product_serial_input` — chỉ chọn bằng `product_item_id`

```graphql
input referral_order_product_serial_input {
  product_item_id: String!   # id của reseller_inventory_product_item
}
```

> **Quan trọng**: input **không** nhận `serial_number`. Serial luôn được lấy từ chính item đã chọn
> (`reseller_inventory_product_item.serial_number`) — server không tin giá trị serial do client gửi.
> Client chỉ chọn *item nào*, không quyết định *serial là gì*.

Cách lấy danh sách item available để đưa vào picker: xem
[list-reseller-inventory-product-item.md](list-reseller-inventory-product-item.md) (query lọc
`sell_order_id IS NULL` + có serial + chưa reserved bằng `_not`).

---

## 2. Luật validate serials

Áp dụng khi `resell_type = "sell_from_inventory"`. Tất cả lỗi trả về dạng GraphQLException với
`extensions.category = "Validate"`.

| Luật | Khi nào | Lỗi |
|---|---|---|
| **Qty khớp serials (strict)** | Khi **submit** (`status = "sent"`) hoặc pay-now | `Serial count (X) must equal quantity (Y) for product <id>` |
| **Qty không vượt (soft)** | Ở **draft** & **preview** | `Too many serials for product <id> (max Y)` (chỉ lỗi khi `count > quantity`) |
| Không trùng item | luôn | `Duplicate product_item_id in serial list` |
| Item tồn tại | luôn | `Product item not found: <id>` |
| **Item thuộc reseller** | luôn | `Product item does not belong to the reseller: <id>` |
| Item khớp product line | luôn | `Product item does not match product line: <id>` |
| Item chưa bán | luôn | `Product item already sold: <id>` |
| Item đã có serial | luôn | `Product item has no serial number yet: <id>` |
| **Chưa reserved ở order khác** | luôn | `Product item already reserved in another order: <id>` |

Ghi chú hành vi:
- **Draft cho chọn thiếu** (`count(serials) <= quantity`) để lưu tiến độ; chỉ ép `count == quantity`
  khi submit/pay.
- **Reserved** = item đang bị 1 `referral_order_product_serial` trỏ vào order khác **chưa** ở trạng
  thái released (`cancelled` / `client_rejected` / `rejected`). Khi **update chính order đang sửa**,
  order đó được loại khỏi điều kiện reserved (không tự báo trùng chính mình).
- **Ownership**: item phải có `reseller_id` = user đang thao tác (fail-closed).
- **Preview** chỉ validate (soft), **không** persist serial.
- Mỗi lần create/update (sell_from_inventory) sẽ **teardown + tạo lại** toàn bộ serial của order từ
  input (không merge từng phần).

---

## 3. Vòng đời serial theo trạng thái order

| Sự kiện | Bảng `referral_order_product_serial` | Bảng `reseller_inventory_product_item` |
|---|---|---|
| Create/Update draft (có serials) | tạo/tái tạo row snapshot (serial copy từ item) | không đổi |
| Submit (`status=sent`) | strict: bắt buộc đủ serial mỗi line | không đổi (`sell_order_id` vẫn NULL — mới chỉ hold) |
| Paid | giữ nguyên | `sell_order_id` được set = order (đánh dấu đã xuất kho) — qua `MarkResellerInventorySoldMessage` |
| Cancelled / rejected | (giữ row) | item được coi là **giải phóng** → available lại cho order khác |

---

## 4. Ví dụ

### Create draft — chọn 1 serial cho line qty=2 (soft OK)

```bash
curl -sk -X POST 'https://localhost/graphql' \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $TOKEN" \
  -H 'x-hasura-role: ROLE_USER' \
  --data-raw '{
    "variables": { "input": {
      "resell_type": "sell_from_inventory",
      "status": "draft",
      "company": "<CLIENT_COMPANY_ID>",
      "shipping_address": { "address": "8160 Mira Mesa Blvd", "city": "San Diego", "state": "CA", "country": "US", "postal_code": "92126", "name": "Kim Truong", "phone": "+18589997162" },
      "is_self_delivery": false, "is_pay_now": false, "create_referral": false,
      "products": [
        {
          "product_id": "<PRODUCT_ID>",
          "quantity": 2,
          "referral_order_product_shipping_product": {},
          "serials": [ { "product_item_id": "<ITEM_ID_1>" } ]
        }
      ]
    }},
    "query": "mutation($input: referral_order_create_mutation_input!){ referral_order_create_mutation(input_obj: $input){ id internal_id status resell_type } }"
  }'
```

### Update submit — phải đủ serial = quantity

```graphql
mutation Submit($id: ID!, $input: referral_order_update_mutation_input!) {
  referral_order_update_mutation(id: $id, input_obj: $input) {
    id
    status
  }
}
```

Variables (line qty=2 → phải đúng 2 item):

```json
{
  "id": "<ORDER_ID>",
  "input": {
    "status": "sent",
    "company": "<CLIENT_COMPANY_ID>",
    "shipping_address": { "address": "...", "city": "San Diego", "state": "CA", "country": "US", "postal_code": "92126", "name": "Kim Truong", "phone": "+18589997162" },
    "is_self_delivery": false, "is_pay_now": false, "create_referral": false,
    "products": [
      {
        "product_id": "<PRODUCT_ID>",
        "quantity": 2,
        "unit_price": 700, "sale_price": 700, "total_after_tax": 700,
        "referral_order_product_shipping_product": {},
        "serials": [
          { "product_item_id": "<ITEM_ID_1>" },
          { "product_item_id": "<ITEM_ID_2>" }
        ]
      }
    ]
  }
}
```

Nếu chỉ truyền 1 serial → lỗi:

```json
{ "errors": [ { "message": "Serial count (1) must equal quantity (2) for product <PRODUCT_ID>",
  "extensions": { "category": "Validate" } } ], "data": { "referral_order_update_mutation": null } }
```

### Preview — validate soft, không persist

```graphql
mutation Preview($input: preview_referral_order_input!) {
  referral_order_preview_order(input_obj: $input) {
    id
    total
    total_after_tax
  }
}
```

---

## 5. Response

Cả 3 mutation trả `referral_order_entity_type` (giữ nguyên schema như trước task 07 — không thêm
field mới ở output). Để đọc lại serial đã gán của order, query Hasura order kèm nested
`referral_order_products.referral_order_product_serials` — xem
[get-order-by-pk.md](get-order-by-pk.md).

```json
{
  "data": {
    "referral_order_create_mutation": {
      "id": "019f5fae-7230-7c08-ba3e-a8f05e1ebcaa",
      "internal_id": 2057,
      "status": "draft",
      "resell_type": "sell_from_inventory"
    }
  }
}
```

---

## Tham chiếu

| Thành phần | File |
|---|---|
| Create resolver | `app/src/GraphQL/ReferralOrder/Mutation/Create/Resolver.php` |
| Update resolver | `app/src/GraphQL/ReferralOrder/Mutation/Update/Resolver.php` |
| Preview resolver | `app/src/GraphQL/ReferralOrder/Mutation/PreviewOrder/Resolver.php` |
| Product input (field `serials`) | `app/src/GraphQL/ReferralOrder/ReferralOrderProductInput.php` |
| Serial input | `app/src/GraphQL/ReferralOrder/ReferralOrderProductSerialInput.php` |
| Validate + sync logic | `app/src/Service/ReferralOrder/ReferralOrderProductSerialService.php` |
| markSold khi paid | `ReferralOrderPaidSubscriber` → `MarkResellerInventorySoldMessage` → `ResellerInventoryProductItemRepository::markSoldByOrder` |
| List item available | [list-reseller-inventory-product-item.md](list-reseller-inventory-product-item.md) |
| Đọc serial của order | [get-order-by-pk.md](get-order-by-pk.md) |
