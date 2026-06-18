# D8-397 — Test Report

**Feature:** Agent Purchase Products & nhập kho riêng (`resell_type` + `agent_product_stock`)
**Môi trường:** Local (docker compose) — Symfony `https://localhost/graphql`, Hasura `http://localhost:8080/v1/graphql`, Postgres `referral`.
**Ngày:** 2026-06-18
**Phạm vi:** Phase 1 (field + CRM sync), Phase 2 (DB structure), Phase 4 (paid → cộng kho), Phase 5 (read API).

---

## Setup / Precondition

### Access token (local)
`/graphql` (Istio authenticator) và Hasura (webhook auth) **không verify chữ ký** ở local → craft token thủ công:

```bash
HEADER=$(printf '%s' '{"alg":"none","typ":"JWT"}' | base64 -w0)
PAYLOAD=$(printf '%s' '{"iss":"https://localhost","sub":"phu_vo@fastboy.net","id":"1f0e616a-8a04-6c62-8f29-63301b77a039","username":"phu_vo@fastboy.net","aud":""}' | base64 -w0)
TOKEN="${HEADER}.${PAYLOAD}.sig"
```
- `iss` = `APP_BASE_URI` (`https://localhost`); `sub` = email (UserProvider load theo email).
- User test: `phu_vo@fastboy.net` (type=agent, isMerchant=true, id `1f0e616a-8a04-6c62-8f29-63301b77a039`).
- Gọi Hasura kèm header `x-hasura-role: ROLE_USER`.

### DB creds
`postgres://fastboy:fastboy@postgres/referral` → `docker compose exec -T postgres psql -U fastboy -d referral`.

---

## Tổng hợp kết quả

| # | Test case | Phase | Kết quả |
|---|---|---|---|
| TC-01 | Create đơn — default `resell_type` | 1 | ✅ PASS |
| TC-02 | Create đơn — `resell_type=purchase_to_inventory` | 1 | ✅ PASS |
| TC-03 | Create đơn — `resell_type` không hợp lệ → reject | 1 | ✅ PASS |
| TC-04 | `sell_from_inventory` bị chặn (RESELL_TYPES_SUPPORTED) | 1 | ✅ PASS |
| TC-05 | Cấu trúc bảng `agent_product_stock` | 2 | ✅ PASS |
| TC-06 | Cột `referral_order.stock_imported_at` | 2 | ✅ PASS |
| TC-07 | Mapping Doctrine khớp DB (re-diff sạch) | 2 | ✅ PASS |
| TC-08 | `upsertIncrement` cộng dồn atomic | 4 | ✅ PASS |
| TC-09 | Handler đăng ký đúng (debug:messenger) | 4 | ✅ PASS |
| TC-10 | E2E: paid → cộng `agent_product_stock` | 4 | ✅ PASS |
| TC-11 | Idempotency: re-fire paid → không cộng 2 lần | 4 | ✅ PASS |
| TC-12 | Commission bị bỏ (order_commission = 0) | 4 | ✅ PASS |
| TC-13 | Read tồn kho qua Hasura + join `crm_product` | 5 | ✅ PASS |
| TC-14 | Permission ROLE_USER chỉ thấy kho mình | 5 | ✅ PASS |
| TC-15 | CI doc-sync `check-generated-docs.sh` | - | ✅ PASS |

**15/15 PASS.** Lưu ý hạn chế ở mục [Chưa cover](#chưa-cover--rủi-ro-còn-lại).

---

## Chi tiết test case

### TC-01 — Create đơn, default `resell_type`
**Mục tiêu:** không truyền `resell_type` → BE lưu `sell_via_crm`.
**Cách test:** mutation `referral_order_create_mutation` (qua Hasura, token agent) với `status=draft`, products, company, shipping_address — KHÔNG có `resell_type`. Select `resell_type` trong response.
**Mong đợi:** `resell_type = "sell_via_crm"`.
**Thực tế:** `{"resell_type":"sell_via_crm", "status":"draft", ...}` — internal_id 2044, DB `resell_type=sell_via_crm`.
**Kết quả:** ✅ PASS

### TC-02 — Create đơn `purchase_to_inventory`
**Mục tiêu:** truyền `resell_type=purchase_to_inventory` → lưu đúng.
**Cách test:** mutation create với `resell_type:"purchase_to_inventory"` + company + shipping_address + products.
**Mong đợi:** `resell_type = "purchase_to_inventory"`, đơn tạo như đơn thường.
**Thực tế:** internal_id 2045 / 2046, DB `resell_type=purchase_to_inventory`.
**Kết quả:** ✅ PASS

### TC-03 — `resell_type` không hợp lệ
**Mục tiêu:** giá trị lạ bị `Assert\Choice` reject.
**Cách test:** mutation với `resell_type:"invalid_value"`.
**Mong đợi:** lỗi validation, field `input_obj.resell_type`.
**Thực tế:** `{"errors":[{"message":"The value you selected is not a valid choice.","extensions":{"category":"Validate","field":"input_obj.resell_type"}}]}`.
**Kết quả:** ✅ PASS

### TC-04 — `sell_from_inventory` bị chặn
**Mục tiêu:** FE không truyền được `sell_from_inventory` (chỉ `RESELL_TYPES_SUPPORTED`).
**Cách test:** mutation với `resell_type:"sell_from_inventory"`.
**Mong đợi:** reject `"not a valid choice"`.
**Thực tế:** reject đúng (field `input_obj.resell_type`). `purchase_to_inventory` cùng lúc vẫn pass validation.
**Kết quả:** ✅ PASS

### TC-05 — Cấu trúc bảng `agent_product_stock`
**Mục tiêu:** table + index + FK đúng spec.
**Cách test:** `docker compose exec -T postgres psql -U fastboy -d referral -c "\d agent_product_stock"`.
**Mong đợi:** cột id/agent_id/product_id/quantity(def 0)/created_at/updated_at; PK id; index agent_id + product_id; unique (agent_id, product_id); FK agent_id→user ON DELETE CASCADE.
**Thực tế:** đủ — `agent_product_stock_pkey`, `idx_e6aa7a8b3414710b` (agent_id), `idx_agent_product_stock_product_id`, `uniq_agent_product_stock_agent_product`, FK CASCADE.
**Kết quả:** ✅ PASS

### TC-06 — Cột `referral_order.stock_imported_at` + `resell_type`
**Cách test:** query `information_schema.columns` cho `referral_order`.
**Mong đợi:** `resell_type` varchar(32) nullable; `stock_imported_at` timestamp nullable.
**Thực tế:** đúng cả 2 (nullable YES).
**Kết quả:** ✅ PASS

### TC-07 — Mapping Doctrine khớp DB
**Cách test:** `php bin/console doctrine:migrations:diff` sau migrate.
**Mong đợi:** diff sinh ra chỉ drift `hdb_catalog` (up rỗng) → mapping khớp.
**Thực tế:** up() rỗng → xóa migration drift. Mapping khớp.
**Kết quả:** ✅ PASS

### TC-08 — `upsertIncrement` cộng dồn atomic
**Mục tiêu:** INSERT...ON CONFLICT cộng dồn đúng + 1 dòng duy nhất.
**Cách test (SQL mô phỏng):**
```sql
INSERT INTO agent_product_stock (...) VALUES (..., qty=10) ON CONFLICT (agent_id, product_id) DO UPDATE SET quantity = quantity + EXCLUDED.quantity;  -- lần 1
-- lần 2 cùng (agent, product) qty=5
```
**Mong đợi:** quantity = 15, 1 dòng.
**Thực tế:** `quantity=15`, 1 row.
**Kết quả:** ✅ PASS

### TC-09 — Handler đăng ký
**Cách test:** `php bin/console debug:messenger | grep AgentProductStock`.
**Mong đợi:** `AddAgentProductStockMessage` handled by `AddAgentProductStockMessageHandler`.
**Thực tế:** đúng.
**Kết quả:** ✅ PASS

### TC-10 — E2E: paid → cộng kho ⭐
**Mục tiêu:** đơn `purchase_to_inventory` paid → worker cộng `agent_product_stock`.
**Cách test:**
1. `docker compose restart worker` (load code mới).
2. Tạo đơn `purchase_to_inventory` (qty 7) qua mutation.
3. `DELETE FROM agent_product_stock;`
4. `UPDATE referral_order SET status='paid' WHERE id='<oid>';` → Hasura event `ReferralOrderPaid` → subscriber → message → worker → handler.
5. Poll `agent_product_stock`.
**Mong đợi:** 1 row `product_id=928ec42a..., quantity=7`; `stock_imported_at` được set.
**Thực tế:** `[t=2s] 928ec42a... | qty=7`; `imported_at=2026-06-18 09:38:06`.
**Kết quả:** ✅ PASS

### TC-11 — Idempotency ⭐
**Mục tiêu:** re-fire paid không cộng kho lần 2.
**Cách test:** sau TC-10 (không reset `stock_imported_at`): `UPDATE status='pending_payment'` → `UPDATE status='paid'` (old≠paid → subscriber dispatch lại). Poll quantity.
**Mong đợi:** quantity vẫn = 7 (handler skip do `stock_imported_at != null`).
**Thực tế:** qty=7 suốt 8s (không đổi).
**Kết quả:** ✅ PASS

### TC-12 — Commission bị bỏ
**Mục tiêu:** đơn `purchase_to_inventory` không tạo commission.
**Cách test:** `SELECT COUNT(*) FROM order_commission WHERE referral_order_id='<oid>';`
**Mong đợi:** 0. Subscriber không dispatch `ChangeUserAmountMessage` (return sớm sau khi dispatch stock message).
**Thực tế:** 0.
**Kết quả:** ✅ PASS (xem hạn chế: nhánh `TransactionUpdateHandler` chưa exercise).

### TC-13 — Read tồn kho + join CRM ⭐
**Mục tiêu:** query `agent_product_stock` + remote relationship `crm_product`.
**Cách test (Hasura, token agent):**
```graphql
query { agent_product_stock(order_by:{updated_at:desc}) {
  product_id quantity updated_at
  crm_product { id name code stock unit_price }
} }
```
**Mong đợi:** trả kho + thông tin sản phẩm CRM.
**Thực tế:**
```json
{"product_id":"928ec42a-...","quantity":15,"crm_product":[{"id":"928ec42a-...","name":"Device: Printer Mount - $15/Each","code":"[DEVE-PRI-0015]","stock":9798,"unit_price":120}]}
```
**Kết quả:** ✅ PASS (field join tên `crm_product`, trả mảng).

### TC-14 — Permission ROLE_USER
**Mục tiêu:** agent chỉ thấy kho của mình (`agent_id = X-Hasura-User-Id`).
**Cách test:** query `agent_product_stock` với token phu_vo.
**Mong đợi:** chỉ trả row có agent_id = phu_vo; cột bị giới hạn theo permission.
**Thực tế:** chỉ thấy kho phu_vo. Permission ROLE_USER filter `agent_id={_eq:X-Hasura-User-Id}` (xem `public_agent_product_stock.yaml`).
**Kết quả:** ✅ PASS

### TC-15 — CI doc-sync
**Mục tiêu:** docs auto-generated khớp source (pipeline gate).
**Cách test:** `bash scripts/check-generated-docs.sh`.
**Mong đợi:** exit 0; `resolvers-catalog.md`, `async-messages.md`, `erd.md` in sync (sau khi regenerate `extract_erd.py` + `extract_async_messages.py`).
**Thực tế:** cả 3 in sync, exit 0. `AddAgentProductStock` xuất hiện trong async-messages catalog.
**Kết quả:** ✅ PASS

---

## Chưa cover / rủi ro còn lại

1. **Commission-skip ở `TransactionUpdateHandler`** — chỉ chạy qua luồng `TransactionUpdate` message (CRM webhook settle). Test e2e đi đường `psql UPDATE status=paid` nên **không exercise** nhánh này. Guard `!isPurchaseToInventory()` đã thêm ở code, cần test với luồng transaction thật (gọi `referral_order_update_status_by_transaction_mutation`).
2. **Trial-skip** (`CreateTrialFromReferralOrderMessageHandler`) — guard đã thêm nhưng chưa có test case kích hoạt trial cho đơn purchase_to_inventory.
3. **Shipping product** — handler bỏ `isShippingProduct`; chưa test đơn có shipping line.
4. **Refund (`paid → cancelled`)** — chưa có cơ chế trừ ngược kho (ngoài scope).
5. **Permission `isAgent` khi create** (Phase 3 optional) — hiện create flow không chặn non-agent tạo `purchase_to_inventory`.
6. **CRM sync `resellType`** (Phase 1) — đã code; chưa có test case quan sát payload gửi sang CRM khi paid (cần luồng paid thật + log CRM).
7. **Dev/prod** — toàn bộ test ở local (token không ký). Trên dev cần token RS256 thật + deploy metadata (remote schema permission, table tracking).

## Ghi chú công cụ
- **ECS** không chạy được (lỗi config `ContainerConfigurator` sẵn có trong repo) — không liên quan thay đổi này.
- Worker phải **restart** sau khi đổi code message handler (`docker compose restart worker`).
- Migration: luôn `diff` rồi dọn drift `CREATE SCHEMA hdb_catalog` trong `down()`.
