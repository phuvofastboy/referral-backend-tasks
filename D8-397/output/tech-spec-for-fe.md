# Tech Spec (FE) — Agent Purchase Products & Nhập kho riêng của Agent

**Task:** D8-397
**Đối tượng:** Frontend
**Tài liệu kỹ thuật BE:** [tech-spec.md](./tech-spec.md)

Tài liệu này mô tả **những gì FE cần biết** để triển khai flow Agent tự mua hàng nhập kho: API thay đổi, GraphQL mutation/query, request & response mẫu.

> **Cập nhật:** field là **`resell_type`** (không phải `order_type`). Field `resell_type` đã **live** (phase 1). Phần kho `agent_product_stock` + strip-down là phase 2.

---

## 1. Tóm tắt cho FE

Thêm **loại đơn mới** qua field `resell_type`. Agent tự mua sản phẩm nhập về **kho riêng** của mình. Khi đơn được thanh toán (`paid`), số lượng tự động cộng vào tồn kho riêng của agent.

### `resell_type` — các giá trị

| Giá trị | Ý nghĩa | FE truyền được? |
|---|---|---|
| `sell_via_crm` | Mặc định — đơn bán cho khách (đơn thường) | ✅ |
| `purchase_to_inventory` | Agent mua hàng nhập kho riêng (**feature này**) | ✅ |
| `sell_from_inventory` | Bán từ kho agent — **chưa hỗ trợ** | ❌ — gửi lên bị reject |

Không gửi `resell_type` → BE mặc định `sell_via_crm`.

**FE cần làm:**
1. Tạo đơn agent mua hàng → `referral_order_create_mutation` với **`resell_type: "purchase_to_inventory"`**.
2. Với đơn `purchase_to_inventory`, **KHÔNG gửi**: `new_client_info`, `company`, `shipping_address`, `services`, `card_type`, `is_self_delivery`. (Strip-down — xem mục 5.)
3. `is_pay_now` luôn bị BE ép `true` (pay-now thẳng, không có bước khách e-sign). FE không render bước ký document.
4. Đọc `resell_type` trên response để phân biệt/lọc đơn.
5. (Tùy nhu cầu) Hiển thị tồn kho agent → query `agent_product_stock` qua Hasura.

> **Không có mutation mới** — tái sử dụng `referral_order_create_mutation`, chỉ thêm field `resell_type`.

---

## 2. Flow tổng quan (ASCII)

```
┌──────────┐                                                  ┌──────────────┐
│  AGENT   │                                                  │ agent_product │
│  (FE)    │                                                  │ _stock (kho)  │
└────┬─────┘                                                  └──────▲────────┘
     │                                                               │
     │ 1. referral_order_create_mutation                            │
     │    { resell_type: "purchase_to_inventory",                   │
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
     │ 2. trả về ReferralOrder { id, total, resell_type, status }    │
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
              │   resell_type = purchase_to_inventory                │
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

FE **không** render màn ký quote/e-sign cho đơn `purchase_to_inventory`.

---

## 3. API thay đổi

### 3.1. Mutation `referral_order_create_mutation`

| Thay đổi | Chi tiết |
|---|---|
| **Input mới** | Field `resell_type: String` — `"sell_via_crm"` \| `"purchase_to_inventory"`. Bỏ trống → default `sell_via_crm`. `sell_from_inventory` bị reject (`Assert\Choice`) |
| **Hành vi** (purchase_to_inventory) | BE bỏ client/shipping/commission/trial/document, ép `is_pay_now = true`, không trừ CRM stock |
| **Quyền** | Chỉ user là **agent** (`isAgent()`) mới tạo được đơn `purchase_to_inventory`, ngược lại lỗi `Permission denied` |

### 3.2. Output type `referral_order_entity_type`

| Thay đổi | Chi tiết |
|---|---|
| **Field mới** | `resell_type: String` — `"sell_via_crm"` \| `"purchase_to_inventory"` \| `null` (đơn cũ ≡ sell_via_crm) |

Có ở **mọi** API trả về `referral_order_entity_type`: `referral_order_create_mutation`, `referral_order_update_mutation`, `referral_order_preview_order`, `referral_order_list_query`, `referral_order_get_detail_for_anonymous`...

### 3.3. (Phase 2) Đọc tồn kho agent — qua Hasura

Bảng `agent_product_stock` được **track trong Hasura** → FE query trực tiếp qua Hasura gateway. Xem mục 4.3.

---

## 4. GraphQL — Request & Response mẫu

### 4.1. Tạo đơn Purchase To Inventory (submit thẳng)

**Mutation:**

```graphql
mutation CreateAgentPurchaseOrder($input: referral_order_create_mutation_input!) {
  referral_order_create_mutation(input_obj: $input) {
    id
    internal_id
    status
    resell_type
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
    "resell_type": "purchase_to_inventory",
    "status": "sent",
    "products": [
      { "product_id": "PRD-12345", "product_name": "Camera ABC", "quantity": 10, "unit_price": 50.0 },
      { "product_id": "PRD-67890", "product_name": "Sensor XYZ", "quantity": 5, "unit_price": 30.0 }
    ]
  }
}
```

> **Không** gửi `new_client_info`, `company`, `shipping_address`, `card_type`, `is_self_delivery`, `services`. `is_pay_now` không cần gửi (BE ép `true`).

**Response mẫu:**

```json
{
  "data": {
    "referral_order_create_mutation": {
      "id": "01890a5d-ac96-774b-bcce-b302099a8057",
      "internal_id": 1024,
      "status": "sent",
      "resell_type": "purchase_to_inventory",
      "is_pay_now": true,
      "total": 650.0,
      "total_after_tax": 650.0,
      "total_tax": 0.0,
      "referral_order_products": [
        { "id": "...111111111111", "product_id": "PRD-12345", "product_name": "Camera ABC", "quantity": 10, "unit_price": 50.0, "total_after_discount": 500.0, "total_after_tax": 500.0 },
        { "id": "...222222222222", "product_id": "PRD-67890", "product_name": "Sensor XYZ", "quantity": 5, "unit_price": 30.0, "total_after_discount": 150.0, "total_after_tax": 150.0 }
      ],
      "created_at": "2026-06-17 10:30:00"
    }
  }
}
```

### 4.2. Lưu nháp (draft)

Giống trên nhưng `status: "draft"` (validate lỏng hơn — chỉ check product tồn tại).

```json
{
  "input": {
    "resell_type": "purchase_to_inventory",
    "status": "draft",
    "products": [ { "product_id": "PRD-12345", "quantity": 10 } ]
  }
}
```

### 4.3. Đọc tồn kho riêng của Agent (qua Hasura — phase 2)

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

**Response mẫu:**

```json
{
  "data": {
    "agent_product_stock": [
      { "id": "...0001", "agent_id": "1f0e616a-8a04-6c62-8f29-63301b77a039", "product_id": "PRD-12345", "quantity": 10, "created_at": "2026-06-17T10:40:00", "updated_at": "2026-06-17T10:40:00" },
      { "id": "...0002", "agent_id": "1f0e616a-8a04-6c62-8f29-63301b77a039", "product_id": "PRD-67890", "quantity": 5,  "created_at": "2026-06-17T10:40:00", "updated_at": "2026-06-17T10:40:00" }
    ]
  }
}
```

> `quantity` là tồn kho **cộng dồn** qua nhiều đơn `purchase_to_inventory` đã `paid`. Mỗi `(agent_id, product_id)` là 1 dòng duy nhất.

### 4.4. Lọc danh sách đơn theo loại

Trong `referral_order_list_query`, đọc thêm `resell_type` để phân biệt/lọc UI (đơn bán khách vs đơn nhập kho agent).

---

## 5. Validation & lưu ý cho FE

| Tình huống | Hành vi BE |
|---|---|
| User không phải agent gọi `resell_type=purchase_to_inventory` | Lỗi `Permission denied` |
| `resell_type = "sell_from_inventory"` hoặc giá trị lạ | Lỗi validation `"not a valid choice"` (`Assert\Choice`, field `input_obj.resell_type`) |
| `products` rỗng khi `status=sent` | Lỗi validation |
| Gửi kèm `company`/`shipping_address` cho `purchase_to_inventory` | **Strip-down**: BE bỏ qua (hoặc reject — chốt khi implement phase 2; FE **không nên** gửi) |
| `is_pay_now` FE gửi `false` | Bị override `true` |
| Không gửi `resell_type` | Default `sell_via_crm`, đơn chạy như cũ |

**Timing cộng kho:** chạy **bất đồng bộ** (sau `paid`, qua queue). FE không kỳ vọng kho cập nhật ngay tại response mutation thanh toán — poll/refetch query stock sau vài giây, hoặc refresh khi mở màn kho.

**Idempotency:** kho cộng **đúng 1 lần** mỗi đơn dù event/queue retry — FE không cần chống trùng.

---

## 6. Checklist FE

- [ ] Màn tạo đơn: chế độ "Agent mua hàng" → set `resell_type="purchase_to_inventory"`, ẩn field client/shipping/card/self-delivery/services.
- [ ] Không render bước ký quote/e-sign cho đơn `purchase_to_inventory`.
- [ ] Đọc & hiển thị `resell_type` trong danh sách/chi tiết; thêm filter theo loại đơn.
- [ ] Màn "Kho của tôi": query `agent_product_stock` qua Hasura, refetch sau thanh toán.
- [ ] Xử lý error `Permission denied` (user không phải agent) + `not a valid choice`.

---

## 7. Tham chiếu nhanh field GraphQL

**Input `referral_order_create_mutation_input` (cho purchase_to_inventory):**

| Field | Type | Bắt buộc | Ghi chú |
|---|---|---|---|
| `resell_type` | String | (khuyến nghị) | `"purchase_to_inventory"`; bỏ trống = `sell_via_crm` |
| `status` | String | ✅ | `"draft"` \| `"sent"` |
| `products` | `[referral_order_product_input!]!` | ✅ (khi sent) | line items |
| `products[].product_id` | String | ✅ | CRM product id |
| `products[].quantity` | Int | ✅ | số lượng nhập |
| `products[].unit_price` | Float | (khi sent) | giá insider |
| `note` / `internal_note` | String | ❌ | tùy chọn |

**Output `referral_order_entity_type`:**

| Field | Type | Ghi chú |
|---|---|---|
| `resell_type` | String | **MỚI** — `sell_via_crm` \| `purchase_to_inventory` \| `null` |
| `id` | ID | UUID |
| `internal_id` | Int | số thứ tự nội bộ |
| `status` | String | trạng thái đơn |
| `is_pay_now` | Boolean | luôn `true` với purchase_to_inventory |
| `total` / `total_after_tax` / `total_tax` | Float | tổng tiền |
| `referral_order_products` | `[referral_order_product_entity_type]` | line items |

**Bảng Hasura `agent_product_stock` (phase 2):**

| Cột | Type | Ghi chú |
|---|---|---|
| `id` | uuid | PK |
| `agent_id` | uuid | = user id của agent |
| `product_id` | string | CRM product id |
| `quantity` | int | tồn kho cộng dồn |
| `created_at` / `updated_at` | timestamp | |
