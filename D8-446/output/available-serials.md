# AvailableSerials

Lấy danh sách serial item **còn bán được** của một reseller cho một product, để picker chọn serial lúc tạo/sửa đơn `sell_from_inventory`. Query chạy trên Hasura view `reseller_inventory_item_available` — view đã tự loại item **đã bán** (`sell_order_id`), item **đang bị giữ** trên đơn chưa-released (draft/sent/signed/pending_payment/paid/decline_payment), và item **chưa có serial**. Vì thế client chỉ cần lọc `product_id` + `reseller_id`, không cần `_not` / `sell_order_id` / `serial_number`.

- **Endpoint:** `POST /api/graphql` (Hasura gateway)
- **Headers:** `authorization: Bearer <JWT>`, `x-hasura-role: ROLE_USER`
- **Permission:** chỉ thấy item của kho mình (`reseller_id = user`) hoặc kho reseller con (`reseller.parent_id = user`).

## Query

```graphql
query AvailableSerials($where: reseller_inventory_item_available_bool_exp!, $limit: Int, $offset: Int) {
  reseller_inventory_item_available(
    where: $where
    order_by: { serial_number: asc }
    limit: $limit
    offset: $offset
  ) {
    id
    serial_number
    __typename
  }
  reseller_inventory_item_available_aggregate(where: $where) {
    aggregate {
      count
      __typename
    }
    __typename
  }
}
```

## Variables

```json
{
  "where": {
    "product_id": { "_eq": "01b3a073-08b3-4502-be63-f2e556b902c1" },
    "reseller_id": { "_eq": "019ed8f5-3e76-7886-bf30-c50e65da418c" }
  },
  "limit": 20,
  "offset": 0
}
```

## Response

```json
{
  "data": {
    "reseller_inventory_item_available": [
      { "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "serial_number": "SN-B", "__typename": "reseller_inventory_item_available" },
      { "id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "serial_number": "SN-C", "__typename": "reseller_inventory_item_available" }
    ],
    "reseller_inventory_item_available_aggregate": {
      "aggregate": { "count": 2, "__typename": "reseller_inventory_item_available_aggregate_fields" },
      "__typename": "reseller_inventory_item_available_aggregate"
    }
  }
}
```
