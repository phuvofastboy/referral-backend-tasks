# API: List `agent_product_stock_available` (tồn khả dụng)

Query Hasura auto-generated trên VIEW `agent_product_stock_available`. So với `agent_product_stock`, view thêm 2 field: **`reserved_qty`** (serial đang bị giữ trên đơn active — status khác `cancelled`/`client_rejected`/`rejected`) và **`available_qty` = `quantity − reserved_qty`** (số thực sự còn bán được, dùng cho màn tạo đơn). `ROLE_USER` tự lọc theo user + `available_qty > 0`.

## Query

```graphql
query GetAgentInventory($where: agent_product_stock_available_bool_exp) {
  agent_product_stock_available(order_by: { available_qty: desc }, where: $where) {
    product_id
    quantity
    reserved_qty
    available_qty
    crm_product {
      id
      name
      code
      stock
      unit_price
      type
      price_for_reseller
      price_for_agent_and_master
    }
  }
}
```

## Variables

`agent_id` = reseller/effective owner (view không có cột `reseller_id`). Nhiều key trong `where` được AND với nhau.

```json
{
  "where": {
    "agent_id": { "_eq": "1f0e616a-8a04-6c62-8f29-63301b77a039" },
    "product_id": { "_eq": "27fb88f8-6846-45aa-b656-18c836812747" },
    "available_qty": { "_gt": 0 }
  }
}
```

## Response

```json
{
  "data": {
    "agent_product_stock_available": [
      {
        "product_id": "4d709176-5696-4800-9f92-f8d1e0d3a7fa",
        "quantity": 18,
        "reserved_qty": 0,
        "available_qty": 18,
        "crm_product": [{ "id": "4d709176-5696-4800-9f92-f8d1e0d3a7fa", "name": "Device: ...", "code": "[DEVE-...]" }]
      },
      {
        "product_id": "f338e2b7-7eb3-4530-ad38-7c8e7107dd25",
        "quantity": 2,
        "reserved_qty": 1,
        "available_qty": 1,
        "crm_product": [{ "id": "f338e2b7-7eb3-4530-ad38-7c8e7107dd25", "name": "Device: ...", "code": "[DEVE-...]" }]
      }
    ]
  }
}
```

> Dòng `f338e2b7`: kho 2 serial, 1 serial bị đơn active hold → còn `available_qty: 1`.
