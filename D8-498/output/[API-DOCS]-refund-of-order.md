# API — Refund của một CRM invoice

Trả danh sách refund **đã hoàn tất** của một CRM invoice, kèm `process_fee`/`tax_amount` đã cộng dồn.
Backend tự lọc theo chủ sở hữu: reseller thấy invoice của mình, agent thêm invoice của con trực tiếp.

- Endpoint: `POST /graphql` (Symfony)
- Header: `Authorization: Bearer <JWT>`, `Content-Type: application/json`
- Query: `crm_order_refund_of_order_query`
- Role: `ROLE_USER`

---

## 1. Query

```graphql
query RefundOfOrder($finInvoiceId: String!) {
  crm_order_refund_of_order_query(fin_invoice_id: $finInvoiceId) {
    id
    internal_id
    total
    process_fee
    tax_amount
  }
}
```

---

## 2. Variables

`fin_invoice_id` là UUID của **CRM `fin_invoice`** — không phải ReferralOrder id, không phải
`order_refund.object_id`.

```json
{ "finInvoiceId": "445f4a4e-3125-418c-b02c-517b366a72c6" }
```

---

## 3. Response

```json
{
  "data": {
    "crm_order_refund_of_order_query": [
      {
        "id": "1b9a84d8-2946-4a61-81e2-19332fd636e9",
        "internal_id": 1453,
        "total": 215,
        "process_fee": 53.75,
        "tax_amount": 18.75
      }
    ]
  }
}
```

---

## 4. Ví dụ khác

### 4.1. Invoice `61989976-9597-4503-9c67-cd52ea68b2dc`

```json
{ "finInvoiceId": "61989976-9597-4503-9c67-cd52ea68b2dc" }
```

```json
{
  "data": {
    "crm_order_refund_of_order_query": [
      {
        "id": "cd62db0d-114c-439b-845b-7510cec43fac",
        "internal_id": 1430,
        "total": 104.27,
        "process_fee": 3.23,
        "tax_amount": 7.5
      }
    ]
  }
}
```

### 4.2. Invoice `01a0331c-1b07-704a-9d78-4f2d15b9f2bd` — có 2 refund, 1 bị lọc

Invoice này có refund `1765` (`cancelled`) và `1766` (`done`). Chỉ `1766` trả về.

```json
{ "finInvoiceId": "01a0331c-1b07-704a-9d78-4f2d15b9f2bd" }
```

```json
{
  "data": {
    "crm_order_refund_of_order_query": [
      {
        "id": "01a0331e-6a4a-73b4-a8cd-d46c60d723b2",
        "internal_id": 1766,
        "total": 280,
        "process_fee": 0,
        "tax_amount": 0
      }
    ]
  }
}
```

### 4.3. Invoice có nhiều refund `done`

Trả nhiều phần tử, sắp theo `internal_id` **giảm dần** (đã đo trên 4 invoice: `[37,36,34]`,
`[101,87,86,83,73,72]`, `[20,13]`, `[3,2,1]`).

⚠️ Row set dưới đây là payload thật của CRM cho invoice `424065`, **không** phải response thật của API
này — invoice đó có `referrer_id = null` nên qua API sẽ ra `[]`. CRM dev không có invoice nào vừa có
chủ vừa có >1 refund `done`, nên đây chỉ minh hoạ shape.

```json
{
  "data": {
    "crm_order_refund_of_order_query": [
      { "id": "0f32a9dd-eb01-40fb-9fd4-28167cd93dbd", "internal_id": 37, "total": 80.17, "process_fee": 14.75, "tax_amount": 0 },
      { "id": "263c6001-be88-4eb8-9010-34aa6c666366", "internal_id": 36, "total": 128.52, "process_fee": 16.24, "tax_amount": 0 },
      { "id": "4502f19b-c4f8-4e58-ac42-2e0f60d05d03", "internal_id": 34, "total": 505.39, "process_fee": 52.22, "tax_amount": 47.17 }
    ]
  }
}
```

---

## 5. Response — không thuộc quyền, hoặc invoice không tồn tại

Cả hai trường hợp trả mảng rỗng, không throw và không phân biệt được với nhau.

```json
{ "data": { "crm_order_refund_of_order_query": [] } }
```

Invoice có `referrer_id = null` (invoice ngoài luồng referral) cũng trả `[]` — không ai sở hữu.

---

## 6. Field reference

| Field | Type | Ghi chú |
| --- | --- | --- |
| `id` | `ID` | UUID của `order_refund` |
| `internal_id` | `Int` | Số hiển thị cho user |
| `total` | `Float` | Tổng tiền refund |
| `process_fee` | `Float` | **Tổng** `order_refund_products.process_fee` |
| `tax_amount` | `Float` | **Tổng** `order_refund_products.tax_amount` |

---

## 7. Ghi chú

**Chỉ trả refund `status = 'done'`.** Filter nằm trong crm-backend, không phải ở đây — refund
`cancelled` / `pending` / `rejected` / `unpaid` không xuất hiện. Cần xem mọi status, hoặc cần field
đầy đủ (`refund_type`, `fin_invoice`, `order_refund_products`, …) thì dùng
[`crm_order_refund_list_query`](../../D8-467/output/%5BAPI-DOCS%5D-order-refund-v2.md) với
`where: { fin_invoice: { id: { _eq: "..." } } }`.

`process_fee` / `tax_amount` do CRM cộng dồn sẵn — `crm_order_refund_list_query` không tính hộ ở
server, FE phải tự SUM `order_refund_products`.

---

## 8. Trạng thái kiểm thử

Số trong mọi example là **data thật của CRM dev**, lấy qua
`crm_fin_invoice_get_list_refund_of_order_query`. Ownership guard đã đo đủ 3 nhánh (đúng chủ / sai chủ
/ agent có con là chủ) và nhánh `referrer_id = null`. **Chưa smoke test end-to-end** qua `/graphql` với
JWT: 4 referrer đang có refund ở CRM dev đều không tồn tại trong bảng `user` local.
