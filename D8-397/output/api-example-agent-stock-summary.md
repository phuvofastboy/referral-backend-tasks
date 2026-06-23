# API — Agent Stock Summary

Trả 4 chỉ số tồn kho của agent đang đăng nhập: tồn hiện tại (`total_stock_unit`) + giá trị tồn theo `crm_product.unit_price` realtime (`total_stock_value`) là **snapshot hiện tại**; số mua về (`total_purchased_unit`, đơn `purchase_to_inventory` đã `paid`) và số bán ra (`total_sold_unit`, đơn `sell_from_inventory` đã `paid`) lọc theo `created_at ∈ [start_date, end_date]`. Agent chỉ xem được kho của chính mình (lấy theo token, không nhận `agent_id`). `total_stock_value` trả `null` nếu CRM lỗi.

## Role

`ROLE_USER` (agent từ `#[InjectUser]`)

## Query

```graphql
query Summary($input: agent_stock_summary_input!) {
  agent_stock_summary_query(input_obj: $input) {
    total_stock_unit
    total_stock_value
    total_purchased_unit
    total_sold_unit
  }
}
```

## Input

```json
{
  "input": {
    "start_date": "2026-01-01T00:00:00+00:00",
    "end_date": "2026-12-31T23:59:59+00:00"
  }
}
```

## Response

```json
{
  "data": {
    "agent_stock_summary_query": {
      "total_stock_unit": 11,
      "total_stock_value": 1610,
      "total_purchased_unit": 3,
      "total_sold_unit": 2
    }
  }
}
```
