# API — Order Refund (CRM) cho ROLE_USER — v2

Đường query mới: **resolver Symfony**, trả list phẳng kèm tổng `count`, thay cho đường v1 đi qua
remote relationship `user { crm_order_refunds }`.

- Endpoint: `POST /graphql` (**Symfony**, không phải `/api/graphql` của Hasura)
- Header: `Authorization: Bearer <JWT>`, `Content-Type: application/json`
- Query: `crm_order_refund_list_query`
- Role: `ROLE_USER`

## Khác gì v1

| | v1 — `user { crm_order_refunds }` | v2 — `crm_order_refund_list_query` |
| --- | --- | --- |
| Endpoint | `/api/graphql` (Hasura) | `/graphql` (Symfony) |
| Shape | Nhóm theo từng row `user` | **List phẳng** |
| Tổng số row | Không có | `count` |
| `limit`/`offset` | Áp cho **từng user row** | Áp cho **cả tập** |
| Sort xuyên nhiều user | Không | Có |
| Phạm vi | self + con trực tiếp (mọi user) | self; **agent** thêm con trực tiếp |
| Lọc `fin_invoice.referrer_id` | Bị gỡ khỏi schema | **Được** (chỉ thu hẹp — mục 4.1) |

v1 vẫn sống song song, không bị gỡ — FE migrate từng màn được.

---

## 1. Phạm vi dữ liệu (backend tự lọc)

Ownership bám vào `fin_invoice.referrer_id`:

| User đăng nhập | Thấy refund của |
| --- | --- |
| `type = 'reseller'` | Chỉ chính mình |
| `type = 'agent'` | Chính mình **+ con trực tiếp** (`user.parent_id = <agent id>`) |

Chỉ **1 tầng** — cháu không với tới, cùng quy tắc với row filter `id = me OR parent_id = me` đang áp
trên bảng `user`. Client không truyền được điều kiện chủ sở hữu, và không cần truyền.

---

## 2. Danh sách refund

### Query

```graphql
query MyOrderRefunds($where: jsonb, $order_by: jsonb, $limit: Int, $offset: Int) {
  crm_order_refund_list_query(
    where: $where
    order_by: $order_by
    limit: $limit
    offset: $offset
  ) {
    count
    data {
      id
      internal_id
      object_id
      refund_type
      status
      type
      sub_total
      total
      total_custom_fields
      created_at
      updated_at
      created_by { id name avatar_link }
      updated_by { id name avatar_link }
      fin_invoice {
        id
        internal_id
        code
        referrer_id
        status
        date
        sub_total
        total_after_tax
        client_company_id
        client_company { id name city state country postal_code whmcs_id }
        referral_order { id internal_id status resell_type }
      }
      order_refund_products {
        id
        custom_product_name
        price
        sale_price
        final_refund_price
        process_fee
        tax_amount
        remaining_amount
        remaining_time
        fin_invoice_product { id name product_name quantity unit_price total_after_tax }
      }
      order_refund_transaction_relations {
        id
        transaction_id
        transaction { a_net_trans_id a_net_trans_status status created_at }
      }
    }
  }
}
```

### Variables

```json
{
  "where": {
    "status": { "_eq": "done" },
    "fin_invoice": {
      "referrer_id": { "_in": ["1f12be2c-d881-689a-aa1a-67fcdf24848f"] }
    }
  },
  "order_by": [{ "created_at": "desc" }],
  "limit": 20,
  "offset": 0
}
```

`fin_invoice.referrer_id` lọc được bình thường — agent dùng để xem refund của **một** con cụ thể thay
vì cả tập. Truyền id không thuộc quyền thì trả rỗng, không phải lỗi. Xem mục 4.1.

Cả 4 argument đều optional. Không truyền gì cả cũng chạy — mặc định `order_by` là
`[{ "created_at": "desc" }]`, `limit`/`offset` bỏ trống thì CRM trả toàn bộ (xem mục 5).

### Response

```json
{
  "data": {
    "crm_order_refund_list_query": {
      "count": 2,
      "data": [
        {
          "id": "01a0331e-6a4a-73b4-a8cd-d46c60d723b2",
          "internal_id": 1766,
          "object_id": "01a0331c-11ec-771e-b550-5e52cb46838b",
          "refund_type": "deposit_balance",
          "status": "done",
          "type": "resell",
          "sub_total": 280,
          "total": 280,
          "total_custom_fields": [],
          "created_at": "2026-08-24T09:33:46",
          "updated_at": "2026-08-24T09:34:30",
          "created_by": {
            "id": "c06bece7-105c-400b-a268-ab9935acc3fa",
            "name": "Ngoc Bui (DEV)",
            "avatar_link": "https://storage.dev-fastboy.dev/crm/storage_objects/av/avatar-c06bece7-....jpg"
          },
          "updated_by": null,
          "fin_invoice": {
            "id": "01a0331c-1b07-704a-9d78-4f2d15b9f2bd",
            "internal_id": 437517,
            "code": null,
            "referrer_id": "019ed496-4206-7680-8829-a24aef6b82cb",
            "status": "paid",
            "date": "2026-08-24T09:31:14",
            "sub_total": 280,
            "total_after_tax": 0,
            "client_company_id": "019fa69a-0b7d-71e6-a10e-c66017bc37fb",
            "client_company": {
              "id": "019fa69a-0b7d-71e6-a10e-c66017bc37fb",
              "name": "cuahangnails",
              "city": "Alaska",
              "state": "AK",
              "country": "US",
              "postal_code": "9001",
              "whmcs_id": 3690
            },
            "referral_order": null
          },
          "order_refund_products": [
            {
              "id": "01a0331e-6a49-7168-87cc-8af72e6ad978",
              "custom_product_name": null,
              "price": 140,
              "sale_price": 140,
              "final_refund_price": 280,
              "process_fee": 0,
              "tax_amount": 0,
              "remaining_amount": 280,
              "remaining_time": 2,
              "fin_invoice_product": {
                "id": "01a0331c-1b08-7130-9456-e8dbf370c1c0",
                "name": "Social Media Marketing: GIF - One-time set-up fee - $150",
                "product_name": "[SMMR-AIM-GROP] - Social Media Marketing: GIF - One-time set-up fee - $150 (08/24/2026 - 10/23/2026)",
                "quantity": 2,
                "unit_price": 140,
                "total_after_tax": 280
              }
            }
          ],
          "order_refund_transaction_relations": []
        }
      ]
    }
  }
}
```

> `count` là **tổng số row khớp filter**, bỏ qua `limit`/`offset` — dùng để phân trang.
> `data` là mảng phẳng, không nhóm theo user; muốn biết refund thuộc về ai thì đọc
> `fin_invoice.referrer_id`.

---

## 3. Chi tiết 1 refund

Không có query riêng — lọc theo `id` với `limit: 1`. Ownership vẫn được áp, nên id của người khác trả
về mảng rỗng.

### Variables

```json
{
  "where": { "id": { "_eq": "01a0331e-6a4a-73b4-a8cd-d46c60d723b2" } },
  "limit": 1
}
```

### Response — không thuộc quyền hoặc không tồn tại

```json
{ "data": { "crm_order_refund_list_query": { "count": 0, "data": [] } } }
```

> Hai trường hợp trả về giống nhau, không phân biệt được — có chủ đích, để không leak sự tồn tại của
> row người khác.

---

## 4. Filter & sort

`where` và `order_by` là scalar `jsonb`, **pass-through nguyên vẹn sang CRM** — FE dùng được toàn bộ
`order_refund_bool_exp` / `order_refund_order_by` mà backend không cần release thêm cho mỗi filter mới.

Backend lồng `where` của client làm nhánh thứ hai của `_and`, cạnh điều kiện ownership:

```json
{ "_and": [
  { "fin_invoice": { "referrer_id": { "_in": ["<me>", "<con 1>", "<con 2>"] } } },
  { "<where của client>": "..." }
]}
```

Nên `where` của client **chỉ thu hẹp được**, không mở rộng: kể cả gửi `_or` thì nó nằm trong nhánh thứ
hai, hai nhánh vẫn phải cùng đúng.

### 4.1. Lọc theo `fin_invoice.referrer_id`

**Được** — và đây là điểm khác v1. Ở v1 Hasura đục bỏ operator này khỏi schema của remote relationship
(`field '_eq' not found in type: 'String_comparison_exp_remote_rel_usercrm_order_refunds'`); ở v2
`where` pass-through nên toàn bộ `fin_invoice_bool_exp` dùng được, kể cả `referrer_id`.

Không thủng, vì kết quả là **giao** của hai nhánh `_and`. Công dụng thật: agent lọc xuống **một** con
cụ thể.

```json
{ "where": { "fin_invoice": { "referrer_id": { "_in": ["<id con 1>", "<id con 2>"] } } } }
```

Đã đo trên CRM dev — giả lập agent sở hữu 2 referrer (`019ed496…` có 2 refund, `019f3b56…` có 1
refund), `1f05bdd0…` là người ngoài (6 refund):

| # | `where` client gửi | `count` | |
| --- | --- | --- | --- |
| A | không gửi | 3 | Toàn bộ tập sở hữu (2 + 1) |
| B | `referrer_id._in: ["019ed496…"]` | 2 | Thu hẹp đúng 1 con |
| C | `referrer_id._eq: "1f05bdd0…"` | **0** | Id người ngoài → rỗng, **không** lỗi |
| D | `_or: [{"019ed496…"}, {"1f05bdd0…"}]` | **2** | `_or` không lách được — chỉ phần sở hữu sống sót |
| E | `where: {}` | 3 | Bằng case A, không mở rộng |

Lưu ý case C: id không thuộc quyền trả mảng rỗng **giống hệt** id không tồn tại — không phân biệt được,
có chủ đích.

### Field lọc được

| Field | Kiểu comparison |
| --- | --- |
| `id` | `uuid_comparison_exp` |
| `internal_id` | `Int_comparison_exp` |
| `object_id`, `refund_type`, `status`, `type` | `String_comparison_exp` |
| `sub_total`, `total` | `float8_comparison_exp` |
| `total_custom_fields` | `jsonb_comparison_exp` |
| `created_at`, `updated_at` | `timestamp_comparison_exp` |
| `fin_invoice` | `fin_invoice_bool_exp` |
| `order_refund_products` | `order_refund_product_bool_exp` |
| `order_refund_products_aggregate` | `order_refund_product_aggregate_bool_exp` |
| `order_refund_transaction_relations` | `order_refund_transaction_relation_bool_exp` |
| `created_by`, `updated_by` | `user_bool_exp` (nhân viên CRM) |
| `_and`, `_or`, `_not` | lồng `order_refund_bool_exp` |

### Ví dụ

```json
{
  "where": {
    "status": { "_eq": "done" },
    "refund_type": { "_in": ["credit_balance", "deposit_balance"] },
    "created_at": { "_gte": "2026-08-01", "_lt": "2026-09-01" },
    "fin_invoice": { "status": { "_eq": "paid" } }
  },
  "order_by": [{ "internal_id": "desc" }]
}
```

`order_by` nhận list, sort nhiều cấp được. Giá trị hướng sort là enum của Hasura truyền dưới dạng
string: `asc`, `desc`, `asc_nulls_first`, `asc_nulls_last`, `desc_nulls_first`, `desc_nulls_last`.
Sort theo field của quan hệ cũng được:

```json
{ "order_by": [{ "fin_invoice": { "date": "desc" } }, { "internal_id": "asc" }] }
```

---

## 5. Phân trang

`limit`/`offset` truyền thẳng sang CRM, **không có trần**. Bỏ trống `limit` là lấy hết số row user đó
sở hữu — với agent nhiều con thì payload có thể rất lớn, FE nên luôn truyền `limit`.

```json
{ "limit": 20, "offset": 40 }
```

Tổng số trang tính từ `count`.

---

## 6. Field reference

### `crm_order_refund_entity_type`

| Field | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | `ID` | |
| `internal_id` | `Int` | Số hiển thị cho user |
| `object_id` | `String` | |
| `refund_type` | `String` | vd `credit_balance`, `deposit_balance` |
| `status` | `String` | vd `done`, `expired` |
| `type` | `String` | vd `resell` |
| `sub_total` | `Float` | |
| `total` | `Float` | |
| `total_custom_fields` | `jsonb` | |
| `created_at` / `updated_at` | `String` | Timestamp CRM, **không timezone**, dạng `2026-08-24T09:33:46` |
| `created_by` / `updated_by` | `crm_user_entity_type` | **Nhân viên CRM**, không phải reseller/agent — id thuộc bảng `user` của CRM, không match `public.user` bên này |
| `fin_invoice` | `crm_fin_invoice_entity_type` | |
| `order_refund_products` | `[crm_order_refund_product_entity_type!]!` | |
| `order_refund_transaction_relations` | `[crm_order_refund_transaction_relation_entity_type!]!` | Thường rỗng — refund trả bằng credit/deposit không chạm payment gateway |

### `crm_fin_invoice_entity_type`

`id: ID`, `internal_id: Int`, `code: String`, `referrer_id: String`, `status: String`, `date: String`,
`valid_until: String`, `sub_total: Float`, `total_after_tax: Float`, `is_new_client: Boolean`,
`is_recheck: Boolean`, `is_send: Boolean`, `new_company: jsonb`, `client_company_id: String`,
`fin_invoice_document_id: String`, `updated_by_id: String`, `created_at: String`, `updated_at: String`,
`client_company: crm_client_company_entity_type`,
`referral_order: referral_order_summary_entity_type`

> `referrer_id` là khoá ownership — chứa id user bên **referral-backend**. Nullable ở CRM: invoice
> ngoài luồng referral có `referrer_id = null` và không bao giờ khớp filter của ai.

### `referral_order_summary_entity_type`

`id: ID`, `internal_id: Int`, `status: String`, `resell_type: String`

Khi map được, block có dạng (cặp thật đã verify: order `internal_id 2043` ↔ invoice `internal_id 436054`):

```json
"referral_order": {
  "id": "019e9710-12e0-7ba1-9cc9-5c01a188a45f",
  "internal_id": 2043,
  "status": "paid",
  "resell_type": null
}
```

Response mẫu ở mục 2 trả `null` vì invoice `437496` chưa có order nào map tới — đúng giá trị đo được.

Đơn referral tương ứng với invoice, join qua `referral_order.crm_internal_id = fin_invoice.internal_id`.
`null` khi chưa map được — `crm_internal_id` được set **async** qua `UpdateCrmOrderMessageHandler` nên
đơn vừa tạo có thể chưa có. Backend resolve cả trang bằng **một** query, không N+1.

### `crm_client_company_entity_type`

`id: ID`, `name: String`, `address1: String`, `city: String`, `state: String`, `country: String`,
`postal_code: String`, `whmcs_id: Int`, `whmcs_id_as_text: String`, `created_at: String`

### `crm_order_refund_product_entity_type`

`id: ID`, `custom_product_id: String`, `custom_product_name: String`, `fin_invoice_product_id: String`,
`final_refund_price: Float`, `price: Float`, `process_fee: Float`, `process_rate: Float`,
`remaining_amount: Float`, `remaining_time: Int`, `sale_price: Float`, `tax_amount: Float`,
`tax_rate: Float`, `total_excluded_fee: Float`,
`fin_invoice_product: crm_fin_invoice_product_entity_type`

### `crm_fin_invoice_product_entity_type`

`id: ID`, `name: String`, `name_qty: String`, `description: String`, `product_id: String`,
`product_name: String`, `product_status: String`, `quantity: Int`, `unit_price: Float`,
`origin_price: Float`, `sale_price: Float`, `discount_amount: Float`, `discount_percent: Float`,
`tax_amount: Float`, `tax_percent: Float`, `taxed: Boolean`, `total_after_discount: Float`,
`total_after_tax: Float`, `total_before_discount: Float`, `sorter_number: Int`, `details: jsonb`

### `crm_order_refund_transaction_relation_entity_type`

`id: ID`, `order_refund_id: String`, `transaction_id: String`, `created_at: String`,
`updated_at: String`, `transaction: crm_transaction_entity_type`

### `crm_transaction_entity_type`

`a_net_trans_id: String`, `a_net_trans_status: String`, `status: String`, `created_at: String`

> CRM chỉ mở 4 field này cho phía referral — không có số tiền ở đây, con số tiền nằm trên chính refund.

### `crm_user_entity_type`

`id: ID`, `name: String`, `avatar_link: String`

---

## 7. Lỗi

### `where` sai field

Lỗi validate của CRM được propagate nguyên văn message. `path` có `_and[1]` vì backend lồng `where`
của client vào nhánh thứ hai:

```json
{
  "errors": [
    {
      "message": "field 'nonexistent_field' not found in type: 'crm_order_refund_bool_exp'",
      "extensions": { "category": "Exception" }
    }
  ]
}
```

### Thiếu / sai token

```json
{ "errors": [ { "message": "You do not have sufficient rights to access this field..." } ] }
```

Thiếu `Bearer `, `iss` không khớp `APP_BASE_URI`, hoặc không có token → request bị coi là ẩn danh.
Header `x-hasura-role` **không** cấp role ở endpoint Symfony này.

---

## 8. Giới hạn đã biết

- Chỉ phủ **1 tầng con** (`parent_id`). Cháu không thấy được.
- Reseller có user con vẫn **không** thấy refund của con — chỉ `type = 'agent'` mới thấy.
- Không có trần `limit` — FE tự giới hạn.
- `where` là `jsonb` nên **không có autocomplete / validate ở tầng schema**; gõ sai field chỉ biết lúc
  runtime, qua message của CRM.
- Đường root cũ của Hasura (`crm_order_refund`, `crm_order_refund_by_pk` ở `/api/graphql`) vẫn còn mở
  cho ROLE_USER và vẫn còn lỗ hổng đọc chéo dữ liệu — ngoài scope task này, xem mục 7 của
  `tasks/D8-467/02-refund-permission/02-plan.md`.

---

## 9. Trạng thái kiểm thử

- Schema: đã verify bằng `graphqlite:dump-schema` — query + 8 type có trong
  `app/tests/_snapshots/graphql-schema.graphqls`.
- Query CRM: đã verify bằng curl thẳng vào Hasura với **full nested selection** ở trên, trả về data
  thật (row ví dụ ở mục 2 là data thật của CRM dev).
- Cơ chế `_and` ở mục 4.1: đã đo cả 5 case (A–E) trên CRM dev bằng `crm_order_refund_aggregate`, số
  `count` trong bảng là số đo thật, không phải suy luận.
- `order_by` lồng quan hệ (`[{fin_invoice:{date:desc}}, {internal_id:asc}]`): đã verify chạy đúng.
- **Chưa smoke test end-to-end** qua `/graphql` với JWT: 4 referrer đang có refund ở CRM dev đều không
  tồn tại trong bảng `user` local, nên không dựng được case dương tính mà không seed data. Cần chạy
  trên môi trường dev.
- Envelope lỗi ở mục 7 lấy từ message CRM thật + đường propagate trong
  `GraphqlErrorsException::throwExceptionFromResponse()`; `extensions` chính xác chưa đo qua HTTP.
