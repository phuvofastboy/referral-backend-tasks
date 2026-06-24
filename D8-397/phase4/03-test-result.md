# Phase 4 — Test Result: `company.is_reseller_inventory`

**Ngày test:** 2026-06-24 · **Môi trường:** local (`https://localhost/graphql` Symfony, `http://localhost:8080/v1/graphql` Hasura gateway)
**Liên quan:** [01-requirement.md](01-requirement.md) · [02-plan.md](02-plan.md)

Verify feature đánh dấu inventory company của reseller + ràng buộc đơn `purchase_to_inventory` + expose/filter qua Hasura.

---

## 1. Dữ liệu test

### User
| Vai trò | Email | ID |
|---|---|---|
| Reseller (canMakeOrder) | `lenguyen@gmail.com` | `019de185-20d6-7474-a95b-824fab7850a2` |

> JWT tạo bằng `tasks/D8-397/skills/smoke-test-graphql-api/gen_token.py` (`--iss https://localhost`), header `x-hasura-role: ROLE_USER`.

### Company
| Loại | ID | Ghi chú |
|---|---|---|
| INV (inventory của lenguyen) | `9cf2d7bb-0f73-4c8d-8841-1a9ebe60937b` | `is_reseller_inventory=true`, `created_by=lenguyen` — **seed bằng SQL** |
| NORMAL | `1f0e6194-7633-6d1c-8199-cbb11ebff8dc` | `is_reseller_inventory=false` |

### Product
| ID | Ghi chú |
|---|---|
| `888368f0-c91a-46f6-88db-e8753482830f` | `allow_for_reseller=true` (agent=150 / reseller=752) |

### SQL seed inventory company
```sql
INSERT INTO company (id, name, email, business_phone, owner_phone, address, postal_code, city, state, country, business_name, is_reseller_inventory, created_by_id, created_at, updated_at)
SELECT gen_random_uuid(), 'LeNguyen Warehouse', 'inv@len.com', '+84932698741', '+84932698741', 'Kho HCM', '70000', 'HCM', 'HCM', 'VN', 'LeNguyen Inv', true, '019de185-20d6-7474-a95b-824fab7850a2', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM company WHERE created_by_id='019de185-20d6-7474-a95b-824fab7850a2' AND is_reseller_inventory=true);
```

---

## 2. Validation — Preview (`referral_order_preview_order`)

Preview validate giống create (không persist). Mutation dùng chung:

```graphql
mutation P($input: preview_referral_order_input!) {
  referral_order_preview_order(input_obj: $input) { id total }
}
```

Khung `input` (thay `resell_type` / `company` / `new_client_info` theo từng case):
```json
{
  "status": "draft",
  "is_self_delivery": false,
  "shipping_address": {"address":"HCM","city":"HCM","country":"VN","name":"t","phone":"+84932698741","postal_code":"70000","state":"HCM"},
  "products": [{"product_id":"888368f0-c91a-46f6-88db-e8753482830f","quantity":1,"referral_order_product_shipping_product":null}],
  "company": "<id>",
  "resell_type": "<type>"
}
```

| # | Case | Input khác biệt | Kỳ vọng | Response | Result |
|---|---|---|---|---|---|
| A | purchase_to_inventory + company NORMAL | `company=NORMAL`, `resell_type=purchase_to_inventory` | reject | `"Company must be your reseller inventory address for a purchase-to-inventory order."` | ✅ PASS |
| B | purchase_to_inventory + company INV | `company=INV`, `resell_type=purchase_to_inventory` | qua gate | `data.total = 10` | ✅ PASS |
| C | purchase_to_inventory + address mới (đã có INV) | bỏ `company`, thêm `new_client_info` đầy đủ | reject | `"You already have a reseller inventory address. Select it instead of entering a new one."` | ✅ PASS |
| D | sell_via_crm + company INV | `company=INV`, `resell_type=sell_via_crm` | reject | `"Reseller inventory company cannot be used for this order type."` | ✅ PASS |
| E | sell_via_crm + company NORMAL (control) | `company=NORMAL`, `resell_type=sell_via_crm` | OK | `data.total = 752` | ✅ PASS |

> **Lưu ý case C:** lần đầu chạy với `new_client_info` thiếu field → bị chặn ở **input validation** (`"This value should not be blank."`) trước khi tới assert. Chạy lại với `new_client_info` đầy đủ (name/email/business_name/business_phone/owner_phone/address/postal_code/city/state/country) mới tới được assert và reject đúng.

`new_client_info` đầy đủ dùng cho C:
```json
"new_client_info": {"name":"Reseller Self","email":"self@len.com","business_name":"Len Biz","business_phone":"+84932698741","owner_phone":"+84932698741","address":"Kho HCM","postal_code":"70000","city":"HCM","state":"HCM","country":"VN"}
```

---

## 3. Create (`referral_order_create_mutation`)

```graphql
mutation C($input: referral_order_create_mutation_input!) {
  referral_order_create_mutation(input_obj: $input) { id status resell_type }
}
```

| # | Case | Input | Kỳ vọng | Response | Result |
|---|---|---|---|---|---|
| CREATE-1 | purchase_to_inventory + company INV | `company=INV`, `resell_type=purchase_to_inventory`, `status=draft` | tạo OK | `id=019ef87c-5cb2-718c-8637-6ebc1b22328f`, `resell_type=purchase_to_inventory` | ✅ PASS |

(Các nhánh reject của create đã được phủ qua Preview ở mục 2 — cùng hàm `assertResellerInventoryCompany`.)

---

## 4. Update (`referral_order_update_mutation`)

Dùng order tạo ở CREATE-1 (`019ef87c-5cb2-718c-8637-6ebc1b22328f`).

```graphql
mutation U($id: ID!, $input: referral_order_update_mutation_input!) {
  referral_order_update_mutation(id: $id, input_obj: $input) { id }
}
```

| # | Case | Input | Kỳ vọng | Response | Result |
|---|---|---|---|---|---|
| UPDATE-1 | đổi company sang NORMAL | `company=NORMAL` | reject | `"Company must be your reseller inventory address for a purchase-to-inventory order."` | ✅ PASS |
| UPDATE-2 | giữ company INV (control) | `company=INV` | OK | `data ... updated` | ✅ PASS |

> `resell_type` immutable trên update → resolver lấy từ `order.getEffectiveResellType()`.

---

## 5. Hasura — company list filter `is_reseller_inventory`

Query qua Hasura gateway (`:8080`), token reseller `lenguyen`:

```graphql
query {
  company(where: {is_reseller_inventory: {_eq: true}}) {
    id name is_reseller_inventory created_by_id
  }
}
```

| # | Case | Kỳ vọng | Response | Result |
|---|---|---|---|---|
| HASURA-1 | filter `is_reseller_inventory=true` | thấy INV company của mình | `[{ id: 9cf2d7bb…, name: "LeNguyen Warehouse", is_reseller_inventory: true, created_by_id: 019de185… }]` | ✅ PASS |
| HASURA-2 | filter `is_reseller_inventory=false` | trả company khác, không có INV | trả company `019de1a2…` (`is_reseller_inventory=false`) | ✅ PASS |

> Xác nhận đồng thời: (a) cột `is_reseller_inventory` expose qua Hasura; (b) filter theo cột hoạt động; (c) permission nhánh `created_by_id = X-Hasura-User-Id` cho reseller thấy inventory company của mình **dù chưa gắn đơn paid nào**.

---

## 6. Kết luận

| Nhóm | Pass | Fail |
|---|---|---|
| Preview validation (A–E) | 5 | 0 |
| Create | 1 | 0 |
| Update | 2 | 0 |
| Hasura filter | 2 | 0 |
| **Tổng** | **10** | **0** |

**Tất cả PASS.** Tiền điều kiện: lint sạch, `doctrine:schema:validate` in sync, `hasura:metadata:apply` OK, migration `Version20260624044513` đã chạy.

### Chưa cover trong đợt này (đề xuất test thêm khi cần)
- **Promote at SIGNED → PAID end-to-end**: đơn `purchase_to_inventory` đi qua e-sign (signed) → company được tạo với `is_reseller_inventory=true` + `created_by=reseller` + **reuse** khi reseller đã có inventory company (helper `promoteCompanyFromClientInfo`). Cần luồng signed thật (Hasura event → worker).
- **`created_by` ở luồng async**: xác nhận company promote qua status/document handler có `created_by`/`updated_by` = reseller (fix Blameable async).
- Reseller chưa có inventory company + tạo `purchase_to_inventory` bằng address → pass (nhánh "first inventory").

### Test data để lại (không xoá)
- INV company `9cf2d7bb-0f73-4c8d-8841-1a9ebe60937b` (lenguyen).
- Order draft `019ef87c-5cb2-718c-8637-6ebc1b22328f` (purchase_to_inventory, lenguyen).
