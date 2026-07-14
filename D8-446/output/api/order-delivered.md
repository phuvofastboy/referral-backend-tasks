# API: Order Delivered (CRM → Referral)

CRM gọi vào khi giao hàng (theo từng **warehouse issue** / phiếu xuất kho) cho đơn
`purchase_to_inventory`. Mỗi lần gọi: ghi các item vật lý (kèm serial) vào kho của reseller
(`reseller_inventory_product_item`) và tính lại tồn khả dụng (`agent_product_stock`).

- **Type**: GraphQL Mutation (GraphQLite)
- **Mutation**: `referral_order_order_delivered_mutation`
- **Endpoint**: `POST /graphql` (dev: `https://localhost/graphql`)
- **Auth (role)**: `ROLE_HASURA_CRM`
- **Áp dụng cho**: chỉ đơn `resell_type = purchase_to_inventory`
- **Idempotent**: theo `warehouse_issue_id`

---

## Authentication

Đây là internal service call (CRM → mesh → Symfony). Không dùng JWT user; xác thực bằng 2 header:

| Header | Giá trị |
|---|---|
| `x-hasura-service` | tên service gọi (vd `crm`) |
| `x-hasura-role` | `ROLE_HASURA_CRM` |

> Chỉ request từ trong service mesh nội bộ mới set được 2 header này.

---

## Request

### GraphQL

```graphql
mutation OrderDelivered($input: referral_order_order_delivered_mutation_input!) {
  referral_order_order_delivered_mutation(input_obj: $input)
}
```

### Input — `referral_order_order_delivered_mutation_input`

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `warehouse_issue_id` | `String` (UUID) | ✅ | ID phiếu xuất kho bên CRM. Dùng để **idempotency** + truy vết. |
| `referral_order_id` | `String` (UUID) | ✅ | ID đơn `purchase_to_inventory`. Phải tồn tại. |
| `issue_items` | `[referral_order_issue_item_input!]` | ✅ (≥ 1) | Danh sách item vật lý được giao trong phiếu này. |

### `referral_order_issue_item_input`

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `product_id` | `String` (UUID) | ✅ | CRM product id. Phải thuộc products của order. |
| `serial_number` | `String` | ✅ | Serial thực của **1 unit** vật lý. Mỗi entry = 1 unit → 1 row inventory. |

> Giao 1 phần được: chỉ gửi số item thực giao trong phiếu này. Các phiếu sau gửi tiếp
> (mỗi phiếu 1 `warehouse_issue_id` khác nhau).

### Variables mẫu

```json
{
  "input": {
    "warehouse_issue_id": "985c0b74-efec-4dca-af73-8557fd8d3df5",
    "referral_order_id": "019f1bec-c622-7199-8387-5377c4805f71",
    "issue_items": [
      { "product_id": "888368f0-c91a-46f6-88db-e8753482830f", "serial_number": "SN-A001" },
      { "product_id": "888368f0-c91a-46f6-88db-e8753482830f", "serial_number": "SN-A002" }
    ]
  }
}
```

### curl

```bash
curl -sk -X POST 'https://localhost/graphql' \
  -H 'content-type: application/json' \
  -H 'x-hasura-service: crm' \
  -H 'x-hasura-role: ROLE_HASURA_CRM' \
  --data-raw '{
    "query": "mutation($input: referral_order_order_delivered_mutation_input!){ referral_order_order_delivered_mutation(input_obj: $input) }",
    "variables": {
      "input": {
        "warehouse_issue_id": "985c0b74-efec-4dca-af73-8557fd8d3df5",
        "referral_order_id": "019f1bec-c622-7199-8387-5377c4805f71",
        "issue_items": [
          { "product_id": "888368f0-c91a-46f6-88db-e8753482830f", "serial_number": "SN-A001" },
          { "product_id": "888368f0-c91a-46f6-88db-e8753482830f", "serial_number": "SN-A002" }
        ]
      }
    }
  }'
```

---

## Response

### Thành công

```json
{ "data": { "referral_order_order_delivered_mutation": true } }
```

Luôn trả `true` khi xử lý xong (kể cả trường hợp idempotent skip).

---

## Hành vi (side effects)

1. **Idempotency**: nếu `warehouse_issue_id` đã được xử lý (đã có item mang id này) → **skip**, không insert lại, trả `true`. CRM retry an toàn.
2. **Insert**: mỗi `issue_items[]` → 1 row `reseller_inventory_product_item`:
   - `reseller_id` = người tạo đơn (`referral_order.created_by`)
   - `product_id`, `serial_number` = theo input
   - `purchase_order_id` = `referral_order_id`
   - `warehouse_issue_id` = theo input
   - `sell_order_id` = `null` (chưa bán)
3. **Recompute** `agent_product_stock` của reseller = số item khả dụng (`sell_order_id IS NULL` + có `serial_number`) theo từng product (SET tuyệt đối, không cộng dồn).
4. Toàn bộ chạy trong 1 transaction — lỗi bất kỳ ⇒ rollback, không ghi gì.

---

## Validation & Errors

Response lỗi theo chuẩn GraphQL (`errors[].message`).

### Lỗi input (validation)

| Điều kiện | Message |
|---|---|
| `warehouse_issue_id` / `referral_order_id` rỗng hoặc không phải UUID | `This value should not be blank.` / lỗi UUID |
| `referral_order_id` không tồn tại | lỗi EntityExist |
| `issue_items` rỗng | `This value should not be blank.` |
| `issue_items[].product_id` không phải UUID | lỗi UUID |
| `issue_items[].serial_number` rỗng | `This value should not be blank.` |

### Lỗi nghiệp vụ (GraphQLException)

| Điều kiện | Message |
|---|---|
| Order không tìm thấy | `Order not found` |
| Order không phải `purchase_to_inventory` | `Order is not a purchase-to-inventory order` |
| Order không có người tạo (reseller) | `Order has no reseller` |
| `product_id` không thuộc products của order | `Issue item product not in order: <product_id>` |

> Mọi lỗi nghiệp vụ đều được log (`logger->error`, `context = referral_order_order_delivered`) kèm `referral_order_id`, `warehouse_issue_id`.

---

## Ghi chú

- Item **chỉ được sinh khi delivered** (qua API này), **không** sinh lúc order paid.
- Sau khi có mặt trong kho (delivered + có serial + chưa bán), item mới **available** để chọn khi bán (`sell_from_inventory`).
- Không còn field `referral_order.delivery_status` — trạng thái giao theo từng warehouse issue, không phải toàn đơn.

## Tham chiếu code

| Thành phần | File |
|---|---|
| Resolver | `app/src/GraphQL/ReferralOrder/Mutation/OrderDelivered/Resolver.php` |
| Input | `app/src/GraphQL/ReferralOrder/Mutation/OrderDelivered/Input.php` |
| Issue item input | `app/src/GraphQL/ReferralOrder/ReferralOrderIssueItemInput.php` |
| Service | `app/src/Service/Stock/ResellerInventoryDeliveryService.php` |
| Repo (insert/idempotency) | `app/src/Repository/Stock/ResellerInventoryProductItemRepository.php` |
| Auth | `app/src/Security/Hasura/HasuraServiceAuthenticator.php` |