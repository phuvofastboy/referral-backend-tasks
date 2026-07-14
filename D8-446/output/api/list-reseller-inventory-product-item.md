# API: List `reseller_inventory_product_item`

Query danh sách kho serial của reseller. Đây là query **Hasura auto-generated** (direct PG mapping),
không phải GraphQLite. Bảng đã được track trong Hasura metadata
(`app/hasura/metadata/sources/default/tables/public_reseller_inventory_product_item.yaml`).

- **Type**: Hasura query (auto-generated)
- **Root fields**: `reseller_inventory_product_item`, `reseller_inventory_product_item_by_pk`, `reseller_inventory_product_item_aggregate`
- **Endpoint**: `POST /v1/graphql` (dev direct: `http://localhost:8080/v1/graphql`; prod qua gateway)
- **Roles**: `ROLE_USER` (row-level: chỉ item của chính reseller), `ROLE_HASURA_CRM` (full)

---

## Authentication & Row-level permission

| Role | Phạm vi thấy |
|---|---|
| `ROLE_USER` | Chỉ item có `reseller_id = X-Hasura-User-Id` |
| `ROLE_HASURA_CRM` | Toàn bộ (no filter) |

- **Client (ROLE_USER)**: gọi qua gateway với JWT; Hasura set `X-Hasura-User-Id` từ claim và tự lọc.
- **Dev/test (admin)**: `x-hasura-admin-secret: <secret>` (bypass permission); có thể giả role bằng `x-hasura-role` + `x-hasura-user-id`.

---

## Columns (được phép select)

| Column | Kiểu | Mô tả |
|---|---|---|
| `id` | `uuid` | PK |
| `reseller_id` | `uuid` | Chủ kho (FK → user) |
| `product_id` | `String` | CRM product id |
| `serial_number` | `String` (nullable) | Serial của unit vật lý |
| `purchase_order_id` | `uuid` | Đơn mua vào (FK → referral_order) |
| `sell_order_id` | `uuid` (nullable) | Đơn bán ra; `NULL` = chưa bán |
| `warehouse_issue_id` | `uuid` (nullable) | Phiếu giao CRM đã tạo item |
| `created_at` / `updated_at` | `timestamp` | |

## Relationships

| Field | Loại | Target |
|---|---|---|
| `purchase_order` | object | `referral_order` (theo `purchase_order_id`) |
| `sell_order` | object | `referral_order` (theo `sell_order_id`) |
| `reseller` | object | `user` (theo `reseller_id`) |
| `referral_order_product_serials` | array | `referral_order_product_serial` |

---

## Query

### List cơ bản + relationships

```graphql
query ListResellerInventory($resellerId: uuid!) {
  reseller_inventory_product_item(
    where: { reseller_id: { _eq: $resellerId } }
    order_by: { created_at: desc }
    limit: 50
    offset: 0
  ) {
    id
    product_id
    serial_number
    warehouse_issue_id
    sell_order_id
    purchase_order { internal_id status resell_type }
    reseller { email }
    referral_order_product_serials { id serial_number }
  }
}
```

### Item còn trong kho (chưa bán + đã có serial)

```graphql
query InStock($resellerId: uuid!, $productId: String!) {
  reseller_inventory_product_item(
    where: {
      reseller_id: { _eq: $resellerId }
      product_id: { _eq: $productId }
      sell_order_id: { _is_null: true }
      serial_number: { _is_null: false }
    }
  ) {
    id
    serial_number
  }
}
```

### Item AVAILABLE để chọn (chưa bán + có serial + CHƯA reserved ở order khác)

"Reserved" = item đang được `referral_order_product_serial` trỏ vào 1 order **chưa** ở trạng thái
released (`cancelled` / `client_rejected` / `rejected`). Lọc bằng `_not` trên array relationship
(`NOT EXISTS` — không có serial nào thuộc order active):

```graphql
query Available($resellerId: uuid!, $productId: String!) {
  reseller_inventory_product_item(
    where: {
      reseller_id: { _eq: $resellerId }
      product_id: { _eq: $productId }
      sell_order_id: { _is_null: true }
      serial_number: { _is_null: false }
      _not: {
        referral_order_product_serials: {
          referral_order_product: {
            referral_order: {
              status: { _nin: ["cancelled", "client_rejected", "rejected"] }
            }
          }
        }
      }
    }
  ) {
    id
    serial_number
  }
}
```

**Khi ĐANG sửa 1 order** (muốn giữ lại item order đó đang hold + item available khác): loại trừ
chính order hiện tại khỏi điều kiện reserved bằng `id: { _neq: $currentOrderId }`:

```graphql
      _not: {
        referral_order_product_serials: {
          referral_order_product: {
            referral_order: {
              _and: [
                { status: { _nin: ["cancelled", "client_rejected", "rejected"] } }
                { id: { _neq: $currentOrderId } }
              ]
            }
          }
        }
      }
```

### Đếm tồn khả dụng theo product (aggregate)

```graphql
query AvailableCount($resellerId: uuid!) {
  reseller_inventory_product_item_aggregate(
    where: {
      reseller_id: { _eq: $resellerId }
      sell_order_id: { _is_null: true }
      serial_number: { _is_null: false }
    }
  ) {
    aggregate { count }
  }
}
```

### By PK

```graphql
query ItemByPk($id: uuid!) {
  reseller_inventory_product_item_by_pk(id: $id) { id serial_number sell_order_id }
}
```

### curl (dev, admin — theo warehouse issue)

```bash
curl -s -X POST http://localhost:8080/v1/graphql \
  -H 'content-type: application/json' \
  -H 'x-hasura-admin-secret: ilovefastboy' \
  --data-raw '{
    "query": "query($wid: uuid!){ reseller_inventory_product_item(where:{warehouse_issue_id:{_eq:$wid}}, order_by:{serial_number:asc}){ id product_id serial_number warehouse_issue_id sell_order_id purchase_order{internal_id status} reseller{email} referral_order_product_serials{id serial_number} } }",
    "variables": { "wid": "985c0b74-efec-4dca-af73-8557fd8d3df5" }
  }'
```

---

## Response mẫu

```json
{
  "data": {
    "reseller_inventory_product_item": [
      {
        "id": "019f596c-b534-7716-b4d0-0ae2a0f0cbd4",
        "product_id": "888368f0-c91a-46f6-88db-e8753482830f",
        "serial_number": "SN-A001",
        "warehouse_issue_id": "985c0b74-efec-4dca-af73-8557fd8d3df5",
        "sell_order_id": null,
        "purchase_order": { "internal_id": 2055, "status": "paid" },
        "reseller": { "email": "lenguyen@gmail.com" },
        "referral_order_product_serials": []
      }
    ]
  }
}
```

---

## Filter / sort / phân trang (Hasura chuẩn)

- **where**: toán tử `_eq`, `_in`, `_is_null`, `_gt`, `_lte`… + `_and`/`_or`/`_not`. Cho phép lọc qua relationship, vd `purchase_order: { status: { _eq: "paid" } }`.
- **order_by**: `{ <column>: asc|desc }` (nhiều cột được).
- **Phân trang**: `limit`, `offset`.

---

## Ghi chú nghiệp vụ

Hai mức "khả dụng":
- **In-stock (chưa bán)** = `sell_order_id IS NULL` + `serial_number IS NOT NULL`.
- **Available (chọn được cho order MỚI)** = in-stock **VÀ chưa reserved** — không có `referral_order_product_serial` nào trỏ item vào order còn active (khác `cancelled`/`client_rejected`/`rejected`). Dùng `_not` như ví dụ "Available" ở trên.

Khác biệt: một item đã bị order khác (draft/sent/paid…) hold serial thì vẫn `sell_order_id NULL` (chỉ set khi order đó paid) nhưng KHÔNG nên cho order mới chọn → cần lớp `_not` để loại.

- Item chỉ xuất hiện sau khi CRM giao hàng (mutation `referral_order_order_delivered_mutation`), không phải lúc order paid. Xem [order-delivered.md](order-delivered.md).
- `ROLE_USER` chỉ thấy kho của chính mình — không cần (và không được) truyền filter `reseller_id`, Hasura tự enforce.

## Tham chiếu

| Thành phần | File |
|---|---|
| Hasura metadata (table + permissions) | `app/hasura/metadata/sources/default/tables/public_reseller_inventory_product_item.yaml` |
| Entity | `app/src/Entity/Stock/ResellerInventoryProductItem.php` |
| Nguồn tạo item | mutation `referral_order_order_delivered_mutation` |
