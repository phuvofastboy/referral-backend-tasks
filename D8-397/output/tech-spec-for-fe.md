# Tech Spec (FE) — Agent Purchase Products & Nhập kho riêng của Agent

**Task:** D8-397
**Đối tượng:** Frontend
**Tài liệu kỹ thuật BE:** [tech-spec.md](./tech-spec.md)

Tài liệu này mô tả **những gì FE cần biết** để triển khai flow Agent tự mua hàng nhập kho: API thay đổi, GraphQL mutation/query, request & response mẫu.

> **Cập nhật:** field là **`resell_type`** (không phải `order_type`). Field `resell_type` đã **live** (phase 1). Đơn `purchase_to_inventory` tạo **y hệt đơn thường** — KHÔNG strip-down; khác biệt chỉ ở BE khi paid. Phần kho `agent_product_stock` là phase 2.

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
2. **Payload tạo đơn GIỐNG đơn thường** — vẫn gửi `company`/`new_client_info`, `shipping_address`, `is_pay_now`,... như bình thường. **Không** cần bỏ field nào. (Khác với bản nháp trước — KHÔNG strip-down.)
3. Đọc `resell_type` trên response để phân biệt/lọc đơn.
4. (Tùy nhu cầu) Hiển thị tồn kho agent → query `agent_product_stock` qua Hasura (sau khi đơn paid).

> Khác biệt của `purchase_to_inventory` nằm ở **phía BE khi đơn paid** (bỏ commission + trial, cộng kho riêng). FE tạo đơn như mọi đơn khác, chỉ set thêm `resell_type`.

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
│  • tạo đơn Y HỆT đơn thường                       │                │
│    (client/shipping/document/CRM stock/tax)      │                │
│  • chỉ lưu thêm resell_type                       │                │
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

### Trạng thái đơn (y hệt đơn thường)

```
draft ──submit──► sent ──► viewed/signed ──► pending_payment ──► paid
                                                                   │
                                                      (cộng agent_product_stock)
```

Vòng đời đơn `purchase_to_inventory` **giống hệt đơn thường** (có e-sign nếu không pay-now). Chỉ khác: khi `paid`, BE cộng kho riêng + bỏ commission/trial.

---

## 3. API thay đổi

### 3.1. Mutation `referral_order_create_mutation`

| Thay đổi | Chi tiết |
|---|---|
| **Input mới** | Field `resell_type: String` — `"sell_via_crm"` \| `"purchase_to_inventory"`. Bỏ trống → default `sell_via_crm`. `sell_from_inventory` bị reject (`Assert\Choice`) |
| **Hành vi** (purchase_to_inventory) | Tạo đơn **y hệt đơn thường** (client/shipping/document/CRM stock/tax). Khác biệt ở phía BE khi `paid`: bỏ commission + trial, cộng `agent_product_stock`. FE **không đổi** payload create |
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

> Ví dụ rút gọn. Thực tế gửi **đầy đủ field như đơn thường** (`company`/`new_client_info`, `shipping_address`, `is_pay_now`, `card_type`,... tùy luồng), chỉ thêm `resell_type`.

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

Query qua Hasura gateway với header `Authorization: Bearer <token>` + `x-hasura-role: ROLE_USER`. Hasura **tự lọc** theo agent đang đăng nhập (`agent_id = X-Hasura-User-Id`) — không cần truyền `agent_id`.

Bảng có **remote relationship `crm_product`** (join `product_id` → CRM) để lấy thông tin sản phẩm trong cùng 1 query:

```graphql
query AgentInventory {
  agent_product_stock(order_by: { updated_at: desc }) {
    product_id
    quantity
    updated_at
    crm_product {        # remote relationship → CRM remote schema (trả về MẢNG)
      id
      name
      code
      stock
      unit_price
    }
  }
}
```

**Response mẫu (thực tế):**

```json
{
  "data": {
    "agent_product_stock": [
      {
        "product_id": "928ec42a-fbc2-449d-bfc9-38296a4701ab",
        "quantity": 15,
        "updated_at": "2026-06-18T08:56:35",
        "crm_product": [
          {
            "id": "928ec42a-fbc2-449d-bfc9-38296a4701ab",
            "name": "Device: Printer Mount - $15/Each",
            "code": "[DEVE-PRI-0015]",
            "stock": 9798,
            "unit_price": 120
          }
        ]
      }
    ]
  }
}
```

**Lưu ý cho FE:**
- Field join tên là **`crm_product`** (không phải `product`).
- `crm_product` trả về **mảng** (do map qua `where: {id: {_eq}}`) → lấy phần tử `[0]`.
- `quantity` là tồn kho **cộng dồn** qua nhiều đơn `purchase_to_inventory` đã `paid`. Mỗi `(agent_id, product_id)` là 1 dòng duy nhất.
- Cột khả dụng cho ROLE_USER: `id, agent_id, product_id, quantity, created_at, updated_at` (+ relationship `crm_product`).

### 4.4. Lọc danh sách đơn theo loại

Trong `referral_order_list_query`, đọc thêm `resell_type` để phân biệt/lọc UI (đơn bán khách vs đơn nhập kho agent).

---

## 5. Validation & lưu ý cho FE

| Tình huống | Hành vi BE |
|---|---|
| User không phải agent gọi `resell_type=purchase_to_inventory` | Lỗi `Permission denied` |
| `resell_type = "sell_from_inventory"` hoặc giá trị lạ | Lỗi validation `"not a valid choice"` (`Assert\Choice`, field `input_obj.resell_type`) |
| `products` rỗng khi `status=sent` | Lỗi validation |
| Gửi kèm `company`/`shipping_address` cho `purchase_to_inventory` | **Bình thường** — BE xử lý như đơn thường (FE **nên** gửi như mọi đơn) |
| Không gửi `resell_type` | Default `sell_via_crm`, đơn chạy như cũ |

**Timing cộng kho:** chạy **bất đồng bộ** (sau `paid`, qua queue). FE không kỳ vọng kho cập nhật ngay tại response mutation thanh toán — poll/refetch query stock sau vài giây, hoặc refresh khi mở màn kho.

**Idempotency:** kho cộng **đúng 1 lần** mỗi đơn dù event/queue retry — FE không cần chống trùng.

---

## 6. Checklist FE

- [ ] Màn tạo đơn: chế độ "Agent mua hàng" → set `resell_type="purchase_to_inventory"`. **Giữ nguyên** các field client/shipping/... như đơn thường.
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
| `is_pay_now` | Boolean | như đơn thường (không bị ép) |
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
