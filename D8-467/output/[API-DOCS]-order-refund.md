# API — Order Refund (CRM) cho ROLE_USER

Refund của CRM được truy cập **qua bảng `user`**, không query thẳng ở root. Hasura tự lọc: mỗi row
`user` trả về là chính user đang đăng nhập hoặc sub-user của họ (`user.id = me OR user.parent_id = me`),
và `crm_order_refunds` bên trong mỗi row chỉ chứa refund của invoice do **chính user đó** làm referrer.
Client không cần (và không thể) tự truyền điều kiện chủ sở hữu.

- Endpoint: `POST /api/graphql` (Hasura)
- Header: `Authorization: Bearer <JWT>`, `Content-Type: application/json`

---

## 1. Danh sách refund

### Query

```graphql
query MyOrderRefunds($limit: Int, $offset: Int) {
  user {
    id
    email
    parent_id
    crm_order_refunds(
      order_by: { created_at: desc }
      limit: $limit
      offset: $offset
    ) {
      id
      internal_id
      object_id
      refund_type
      status
      sub_total
      total
      created_at
      updated_at
      fin_invoice {
        id
        internal_id
        referrer_id
        client_company_id
        date
      }
    }
  }
}
```

### Variables

```json
{ "limit": 20, "offset": 0 }
```

### Response

```json
{
  "data": {
    "user": [
      {
        "id": "1f0f0684-5602-697c-a16d-7d291904f55f",
        "email": "nguyen_dao@fastboy.net",
        "parent_id": null,
        "crm_order_refunds": [
          {
            "id": "cd62db0d-114c-439b-845b-7510cec43fac",
            "internal_id": 1430,
            "object_id": null,
            "refund_type": "credit_balance",
            "status": "done",
            "sub_total": 104.27,
            "total": 104.27,
            "created_at": "2025-07-31T06:59:31",
            "updated_at": "2025-07-31T06:59:31",
            "fin_invoice": {
              "id": "486aed48-6cc1-41a0-aeb1-7191559ad0e8",
              "internal_id": 432401,
              "referrer_id": "1f0f0684-5602-697c-a16d-7d291904f55f",
              "client_company_id": "31348ec2-6f23-4814-9a56-8ac5167f6423",
              "date": "2025-07-16T04:31:34"
            }
          }
        ]
      },
      {
        "id": "1f12be2c-d881-689a-aa1a-67fcdf24848f",
        "email": "nguyen_dao_sub@gmail.com",
        "parent_id": "1f0f0684-5602-697c-a16d-7d291904f55f",
        "crm_order_refunds": []
      }
    ]
  }
}
```

> Kết quả **nhóm theo user**, không phải một mảng phẳng — FE tự gộp nếu cần hiển thị chung một list.
> `limit`/`offset` áp cho **từng user row**, không phải cho tổng.

---

## 2. Chi tiết 1 refund (thay cho `order_refund_by_pk`)

### Query

```graphql
query OrderRefundDetail($id: uuid!) {
  user {
    crm_order_refunds(where: { id: { _eq: $id } }, limit: 1) {
      id
      internal_id
      refund_type
      status
      sub_total
      total
      total_custom_fields
      created_at
      fin_invoice { id internal_id referrer_id date }
      order_refund_products {
        id
        custom_product_name
        price
        sale_price
        final_refund_price
        tax_amount
        remaining_amount
        remaining_time
      }
    }
  }
}
```

### Variables

```json
{ "id": "cd62db0d-114c-439b-845b-7510cec43fac" }
```

### Response — không thuộc quyền hoặc không tồn tại

```json
{ "data": { "user": [ { "crm_order_refunds": [] }, { "crm_order_refunds": [] } ] } }
```

> Cả hai trường hợp đều trả mảng rỗng, không có lỗi và không phân biệt được với nhau.

---

## 3. Filter & sort

Truyền thêm `where` vào `crm_order_refunds` — Hasura **AND** nó vào điều kiện chủ sở hữu, chỉ thu hẹp
được chứ không mở rộng.

```graphql
crm_order_refunds(
  where: {
    status: { _eq: "done" }
    refund_type: { _eq: "credit_balance" }
    created_at: { _gte: "2025-07-01" }
  }
  order_by: { created_at: desc }
)
```

| Field lọc được | Kiểu |
| --- | --- |
| `id` | `uuid_comparison_exp` |
| `internal_id` | `Int_comparison_exp` |
| `object_id`, `refund_type`, `status`, `type` | `String_comparison_exp` |
| `sub_total`, `total` | `float8_comparison_exp` |
| `created_at`, `updated_at` | `timestamp_comparison_exp` |
| `fin_invoice` | bool_exp (đã gỡ `referrer_id._eq`) |
| `order_refund_products`, `order_refund_transaction_relations` | bool_exp |

`order_by`, `limit`, `offset`, `distinct_on` dùng bình thường.

---

## 4. Lỗi

Tự set `referrer_id` để đọc data người khác sẽ bị chặn ở tầng validate:

```json
{
  "errors": [
    {
      "message": "field '_eq' not found in type: 'String_comparison_exp_remote_rel_usercrm_order_refunds'",
      "extensions": {
        "path": "$.selectionSet.user.selectionSet.crm_order_refunds.args.where.fin_invoice.referrer_id._eq",
        "code": "validation-failed"
      }
    }
  ]
}
```

---

## Ghi chú

- Response mục 1 là **ví dụ minh hoạ**: các field refund lấy từ data thật của CRM dev, còn cặp
  user/refund được ghép để minh hoạ — trên local chưa có user nào trùng referrer nên query thật trả
  `crm_order_refunds: []`.
- Đường cũ `crm_order_refund(...)` / `crm_order_refund_by_pk(...)` ở root **vẫn còn hoạt động**, chưa
  bị gỡ. Không dùng cho tính năng mới.
