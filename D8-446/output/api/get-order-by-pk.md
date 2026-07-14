# API: Get Order By PK (kèm serials)

Lấy 1 order theo id, kèm nested `referral_order_products` → `referral_order_product_serials`
(serial được gán cho từng product line, cùng inventory item nguồn). Đây là query **Hasura
auto-generated** (direct PG mapping), không phải GraphQLite.

- **Type**: Hasura query (auto-generated)
- **Root field**: `referral_order_by_pk(id: uuid!)` (list dùng `referral_order`)
- **Endpoint**: `POST /v1/graphql` (dev direct: `http://localhost:8080/v1/graphql`; prod qua gateway)
- **Roles**: `ROLE_USER` (row-level), `ROLE_HASURA_CRM` (full)

---

## Relationship chain

```
referral_order
  └─ referral_order_products        (array → referral_order_product)
       └─ referral_order_product_serials   (array → referral_order_product_serial)
            ├─ reseller_inventory_product_item  (object → item nguồn trong kho)
            └─ referral_order_product           (object → ngược lại line)
```

## Authentication & Row-level permission

| Bảng | ROLE_USER thấy khi | ROLE_HASURA_CRM |
|---|---|---|
| `referral_order` | `created_by_id` = user, HOẶC `created_by_user.parent_id` = user, HOẶC `owner_id` = user | full |
| `referral_order_product` | không filter (gated theo order cha) | full |
| `referral_order_product_serial` | order của serial có `owner_id` = user HOẶC `owner.parent_id` = user | full |

- `X-Hasura-User-Id` do Hasura set từ JWT claim (qua gateway). `by_pk` trả `null` nếu không thoả filter.
- Dev/test: `x-hasura-admin-secret` (bypass) hoặc giả role bằng `x-hasura-role` + `x-hasura-user-id`.

---

## Query

```graphql
query GetOrderByPk($id: uuid!) {
  referral_order_by_pk(id: $id) {
    id
    internal_id
    status
    resell_type
    total
    total_after_tax

    referral_order_products {
      id
      product_id
      product_name
      quantity
      unit_price
      is_shipping_product

      referral_order_product_serials {
        id
        serial_number
        product_item_id
        reseller_inventory_product_item {
          id
          serial_number
          warehouse_issue_id
          purchase_order_id
          sell_order_id
        }
      }
    }
  }
}
```

### curl (dev, admin)

```bash
curl -s -X POST http://localhost:8080/v1/graphql \
  -H 'content-type: application/json' \
  -H 'x-hasura-admin-secret: ilovefastboy' \
  --data-raw '{
    "query": "query($id: uuid!){ referral_order_by_pk(id:$id){ id internal_id status resell_type referral_order_products{ id product_id quantity referral_order_product_serials{ id serial_number reseller_inventory_product_item{ id serial_number warehouse_issue_id sell_order_id } } } } }",
    "variables": { "id": "019f1bec-c622-7199-8387-5377c4805f71" }
  }'
```

### curl (ROLE_USER — giả role, dev)

```bash
curl -s -X POST http://localhost:8080/v1/graphql \
  -H 'content-type: application/json' \
  -H 'x-hasura-admin-secret: ilovefastboy' \
  -H 'x-hasura-role: ROLE_USER' \
  -H 'x-hasura-user-id: 019de185-20d6-7474-a95b-824fab7850a2' \
  --data-raw '{ "query": "query($id: uuid!){ referral_order_by_pk(id:$id){ id status } }", "variables": { "id": "019f1bec-c622-7199-8387-5377c4805f71" } }'
```

---

## Response mẫu

```json
{
  "data": {
    "referral_order_by_pk": {
      "id": "019f1bec-c622-7199-8387-5377c4805f71",
      "internal_id": 2055,
      "status": "paid",
      "resell_type": "sell_from_inventory",
      "referral_order_products": [
        {
          "id": "019f1bec-c624-7d7f-af3e-2025d4c53402",
          "product_id": "888368f0-c91a-46f6-88db-e8753482830f",
          "quantity": 1,
          "referral_order_product_serials": [
            {
              "id": "09f64030-fd10-4b1e-9042-ee5945d0bb96",
              "serial_number": "SN-A001",
              "reseller_inventory_product_item": {
                "id": "019f596c-b534-7716-b4d0-0ae2a0f0cbd4",
                "serial_number": "SN-A001",
                "warehouse_issue_id": "985c0b74-efec-4dca-af73-8557fd8d3df5",
                "sell_order_id": "019f1bec-c622-7199-8387-5377c4805f71"
              }
            }
          ]
        }
      ]
    }
  }
}
```

> Product line không có serial gán sẽ trả `referral_order_product_serials: []`.

---

## Ghi chú

- `referral_order_product_serials` chỉ có dữ liệu khi order đã được gán serial (flow `sell_from_inventory` — **hiện tạm tắt**, xem TODO(D8-446) trong resolver create/update/preview). Trước khi bật lại, mảng này thường rỗng.
- Serial trên bảng nối là **snapshot**; item nguồn thật nằm ở `reseller_inventory_product_item` (relationship object).
- `by_pk` chỉ nhận đúng 1 arg `id` (UUID). Cần lọc/nhiều bản ghi → dùng root field `referral_order(where: ..., order_by: ..., limit: ...)`.

## Tham chiếu

| Thành phần | File |
|---|---|
| Hasura metadata order | `app/hasura/metadata/sources/default/tables/public_referral_order.yaml` |
| Hasura metadata product (rel `referral_order_product_serials`) | `app/hasura/metadata/sources/default/tables/public_referral_order_product.yaml` |
| Hasura metadata serial | `app/hasura/metadata/sources/default/tables/public_referral_order_product_serial.yaml` |
| API list inventory item | [list-reseller-inventory-product-item.md](list-reseller-inventory-product-item.md) |
