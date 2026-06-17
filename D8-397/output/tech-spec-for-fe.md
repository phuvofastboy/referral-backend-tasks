# Tech Spec (FE) — Agent Purchase Products & Nhập kho riêng của Agent

**Task:** D8-397
**Đối tượng:** Frontend
**Tài liệu kỹ thuật BE:** [tech-spec.md](./tech-spec.md)

Tài liệu này mô tả **những gì FE cần biết** để triển khai flow Agent tự mua hàng nhập kho: API thay đổi, GraphQL mutation/query, request & response mẫu.

---

## 1. Tóm tắt cho FE

Thêm một **loại đơn mới**: `agent_purchase` — Agent tự mua sản phẩm để nhập về **kho riêng** của mình (thay vì bán cho khách). Khi đơn được thanh toán (`paid`), số lượng sản phẩm tự động cộng vào tồn kho riêng của agent.

**FE cần làm:**
1. Khi tạo đơn agent mua hàng → gọi `referral_order_create_mutation` **với field mới `order_type: "agent_purchase"`**.
2. Với đơn `agent_purchase`, **KHÔNG cần gửi**: `new_client_info`, `company`, `shipping_address`, `services`, `card_type`, `is_self_delivery`. (Nếu gửi sẽ bị bỏ qua / có thể bị reject — xem mục 5.)
3. `is_pay_now` luôn được BE ép = `true` (đơn đi thẳng pay-now, không có bước khách e-sign). FE không cần render bước ký document.
4. Đọc field mới `order_type` trên response để phân biệt loại đơn (lọc/hiển thị danh sách).
5. (Tùy nhu cầu) Hiển thị tồn kho agent → query bảng `agent_product_stock` qua Hasura.

> **Không có mutation mới** cho việc tạo đơn — tái sử dụng `referral_order_create_mutation` hiện có, chỉ thêm field `order_type`.

---

## 2. Flow tổng quan (ASCII)

```
┌──────────┐                                                  ┌──────────────┐
│  AGENT   │                                                  │ agent_product │
│  (FE)    │                                                  │ _stock (kho)  │
└────┬─────┘                                                  └──────▲────────┘
     │                                                               │
     │ 1. referral_order_create_mutation                            │
     │    { order_type: "agent_purchase",                           │
     │      status: "sent", products: [...] }                       │
     │                                                               │
     ▼                                                               │
┌─────────────────────────────────────────────────┐                │
│              referral-backend (BE)               │                │
│  • check isAgent()                               │                │
│  • lấy insider price từ CRM                      │                │
│  • KHÔNG client/shipping/commission/trial        │                │
│  • KHÔNG tạo document, KHÔNG trừ CRM stock       │                │
│  • force is_pay_now = true                        │                │
└────┬─────────────────────────────────────────────┘                │
     │ 2. trả về ReferralOrder { id, total, order_type, status }     │
     ▼                                                               │
┌──────────┐  3. thanh toán (thẻ agent, qua CRM/gateway)            │
│  AGENT   │ ───────────────────────────────────────────►          │
│  (FE)    │                                                         │
└──────────┘                                                         │
                                                                     │
        4. CRM webhook → BE set status = paid                        │
                            │                                        │
                            ▼                                        │
              ┌───────────────────────────┐                         │
              │ Hasura event ReferralOrderPaid                       │
              │   order_type = agent_purchase                        │
              └────────────┬──────────────┘                         │
                           │ async (RabbitMQ)                        │
                           ▼                                         │
              ┌───────────────────────────┐   5. quantity += qty    │
              │ AddAgentProductStock       │ ────────────────────────┘
              │ Handler (idempotent upsert)│
              └───────────────────────────┘
```

### Trạng thái đơn (giống đơn thường, nhưng skip e-sign)

```
draft ──submit──► sent ──(pay-now)──► signed ──► pending_payment ──► paid
                                                                       │
                                                          (cộng agent_product_stock)
```

FE **không** render màn ký quote/e-sign cho đơn `agent_purchase`.

---

## 3. API thay đổi

### 3.1. Mutation `referral_order_create_mutation`

| Thay đổi | Chi tiết |
|---|---|
| **Input mới** | Thêm field `order_type: String` — giá trị `"agent_purchase"` hoặc bỏ trống (đơn thường) |
| **Hành vi** | Khi `order_type = "agent_purchase"`: BE bỏ qua client/shipping/commission/trial/document, ép `is_pay_now = true`, không trừ CRM stock |
| **Quyền** | Chỉ user là **agent** (`isAgent()`) mới tạo được đơn `agent_purchase`, ngược lại lỗi `Permission denied` |

### 3.2. Output type `referral_order_entity_type`

| Thay đổi | Chi tiết |
|---|---|
| **Field mới** | `order_type: String` — `"agent_purchase"` hoặc `null` (đơn thường). FE dùng để lọc/hiển thị |

Field mới này có mặt ở **mọi** API trả về `referral_order_entity_type`:
`referral_order_create_mutation`, `referral_order_update_mutation`, `referral_order_preview_order`, `referral_order_list_query`, `referral_order_get_detail_for_anonymous`...

### 3.3. (Mới) Đọc tồn kho agent — qua Hasura

Bảng `agent_product_stock` được **track trong Hasura** → FE query trực tiếp qua Hasura GraphQL gateway (không cần mutation BE riêng). Xem mục 4.3.

---

## 4. GraphQL — Request & Response mẫu

### 4.1. Tạo đơn Agent Purchase (submit thẳng)

**Mutation:**

```graphql
mutation CreateAgentPurchaseOrder($input: referral_order_create_mutation_input!) {
  referral_order_create_mutation(input_obj: $input) {
    id
    internal_id
    status
    order_type
    is_pay_now
    total
    total_after_tax
    total_tax
    referral_order_products {
      id
      product_id
      product_name
      quantity
      unit_price
      total_after_discount
      total_after_tax
    }
    created_at
  }
}
```

**Variables:**

```json
{
  "input": {
    "order_type": "agent_purchase",
    "status": "sent",
    "products": [
      {
        "product_id": "PRD-12345",
        "product_name": "Camera ABC",
        "quantity": 10,
        "unit_price": 50.0
      },
      {
        "product_id": "PRD-67890",
        "product_name": "Sensor XYZ",
        "quantity": 5,
        "unit_price": 30.0
      }
    ]
  }
}
```

> Lưu ý: **không** gửi `new_client_info`, `company`, `shipping_address`, `card_type`, `is_self_delivery`, `services`. `is_pay_now` không cần gửi (BE tự ép `true`).

**Response mẫu:**

```json
{
  "data": {
    "referral_order_create_mutation": {
      "id": "01890a5d-ac96-774b-bcce-b302099a8057",
      "internal_id": 1024,
      "status": "sent",
      "order_type": "agent_purchase",
      "is_pay_now": true,
      "total": 650.0,
      "total_after_tax": 650.0,
      "total_tax": 0.0,
      "referral_order_products": [
        {
          "id": "01890a5d-ad10-7000-9aaa-111111111111",
          "product_id": "PRD-12345",
          "product_name": "Camera ABC",
          "quantity": 10,
          "unit_price": 50.0,
          "total_after_discount": 500.0,
          "total_after_tax": 500.0
        },
        {
          "id": "01890a5d-ad10-7000-9aaa-222222222222",
          "product_id": "PRD-67890",
          "product_name": "Sensor XYZ",
          "quantity": 5,
          "unit_price": 30.0,
          "total_after_discount": 150.0,
          "total_after_tax": 150.0
        }
      ],
      "created_at": "2026-06-17 10:30:00"
    }
  }
}
```

### 4.2. Lưu nháp (draft) đơn Agent Purchase

Giống trên nhưng `status: "draft"`. Khi draft, validate lỏng hơn (chỉ kiểm tra product tồn tại). FE có thể cho agent lưu giỏ rồi submit sau.

```json
{
  "input": {
    "order_type": "agent_purchase",
    "status": "draft",
    "products": [
      { "product_id": "PRD-12345", "quantity": 10 }
    ]
  }
}
```

### 4.3. Đọc tồn kho riêng của Agent (qua Hasura)

```graphql
query AgentProductStock($agentId: uuid!) {
  agent_product_stock(
    where: { agent_id: { _eq: $agentId } }
    order_by: { updated_at: desc }
  ) {
    id
    agent_id
    product_id
    quantity
    created_at
    updated_at
  }
}
```

**Variables:**

```json
{ "agentId": "01890a5d-7777-7000-aaaa-000000000001" }
```

**Response mẫu:**

```json
{
  "data": {
    "agent_product_stock": [
      {
        "id": "01890a99-1111-7000-bbbb-000000000001",
        "agent_id": "01890a5d-7777-7000-aaaa-000000000001",
        "product_id": "PRD-12345",
        "quantity": 10,
        "created_at": "2026-06-17T10:40:00",
        "updated_at": "2026-06-17T10:40:00"
      },
      {
        "id": "01890a99-1111-7000-bbbb-000000000002",
        "agent_id": "01890a5d-7777-7000-aaaa-000000000001",
        "product_id": "PRD-67890",
        "quantity": 5,
        "created_at": "2026-06-17T10:40:00",
        "updated_at": "2026-06-17T10:40:00"
      }
    ]
  }
}
```

> `quantity` là tồn kho **cộng dồn** qua nhiều đơn `agent_purchase` đã `paid`. Mỗi `(agent_id, product_id)` là 1 dòng duy nhất.

### 4.4. Lọc danh sách đơn theo loại

Trong `referral_order_list_query`, FE đọc thêm `order_type` để phân biệt/lọc UI (đơn bán khách vs đơn nhập kho agent):

```graphql
query OrderList {
  referral_order_list_query(/* filter hiện có */) {
    # ... các field list hiện có
    # mỗi item có thêm: order_type
  }
}
```

---

## 5. Validation & lưu ý cho FE

| Tình huống | Hành vi BE |
|---|---|
| User không phải agent gọi `order_type=agent_purchase` | Lỗi `Permission denied` |
| `products` rỗng khi `status=sent` | Lỗi validation (products bắt buộc khi submit) |
| Gửi kèm `new_client_info`/`company`/`shipping_address` cho đơn agent_purchase | BE bỏ qua (hoặc reject — sẽ chốt khi implement; FE **không nên** gửi) |
| `is_pay_now` FE gửi `false` | Bị override `true` |
| `order_type` giá trị lạ (ngoài `agent_purchase`) | Lỗi validation (`Assert\Choice`) |
| Đơn thường (không gửi `order_type`) | Hoạt động y như cũ, `order_type = null` trong response |

**Timing cộng kho:** việc cộng `agent_product_stock` chạy **bất đồng bộ** (sau khi `paid`, qua queue). FE không nên kỳ vọng kho cập nhật ngay tại response của mutation thanh toán — cần poll/refetch query stock sau vài giây, hoặc refresh khi user mở màn kho.

**Idempotency:** kho chỉ cộng **đúng 1 lần** cho mỗi đơn dù event/queue retry — FE không cần xử lý chống trùng.

---

## 6. Checklist FE

- [ ] Màn tạo đơn: thêm chế độ "Agent mua hàng" → set `order_type="agent_purchase"`, ẩn các field client/shipping/card/self-delivery/services.
- [ ] Không render bước ký quote/e-sign cho đơn `agent_purchase`.
- [ ] Đọc & hiển thị `order_type` trong danh sách/chi tiết đơn; thêm filter theo loại đơn.
- [ ] Màn "Kho của tôi": query `agent_product_stock` qua Hasura, refetch sau khi thanh toán.
- [ ] Xử lý error `Permission denied` (user không phải agent).

---

## 7. Tham chiếu nhanh field GraphQL

**Input `referral_order_create_mutation_input` (field dùng cho agent_purchase):**

| Field | Type | Bắt buộc | Ghi chú |
|---|---|---|---|
| `order_type` | String | ✅ | `"agent_purchase"` |
| `status` | String | ✅ | `"draft"` \| `"sent"` |
| `products` | `[referral_order_product_input!]!` | ✅ (khi sent) | line items |
| `products[].product_id` | String | ✅ | CRM product id |
| `products[].quantity` | Int | ✅ | số lượng nhập |
| `products[].unit_price` | Float | (khi sent) | giá insider |
| `note` / `internal_note` | String | ❌ | tùy chọn |

**Output `referral_order_entity_type` (field mới + thường dùng):**

| Field | Type | Ghi chú |
|---|---|---|
| `order_type` | String | **MỚI** — `"agent_purchase"` \| `null` |
| `id` | ID | UUID |
| `internal_id` | Int | số thứ tự nội bộ |
| `status` | String | trạng thái đơn |
| `is_pay_now` | Boolean | luôn `true` với agent_purchase |
| `total` / `total_after_tax` / `total_tax` | Float | tổng tiền |
| `referral_order_products` | `[referral_order_product_entity_type]` | line items |

**Bảng Hasura `agent_product_stock`:**

| Cột | Type | Ghi chú |
|---|---|---|
| `id` | uuid | PK |
| `agent_id` | uuid | = user id của agent |
| `product_id` | string | CRM product id |
| `quantity` | int | tồn kho cộng dồn |
| `created_at` / `updated_at` | timestamp | |
