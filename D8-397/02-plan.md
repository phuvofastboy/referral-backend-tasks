# D8-397 — Implementation Plan

**Feature:** Agent Purchase Products & nhập kho riêng của Agent (`resell_type`)
**Tài liệu:** [01-requirement.md](01-requirement.md) · [output/tech-spec.md](output/tech-spec.md) · [output/tech-spec-for-fe.md](output/tech-spec-for-fe.md)

Mỗi phase = 1 deliverable cụ thể, tự hoàn thành & verify được. Làm tuần tự P2 → P6.

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 1 | Field `resell_type` + CRM sync | ✅ **DONE** |
| 2 | Cấu trúc DB: bảng `agent_product_stock` + `stock_imported_at` | ✅ **DONE** |
| 3 | (Optional) Permission `isAgent` cho `purchase_to_inventory` — create flow KHÔNG đổi | ☐ TODO (optional) |
| 4 | Paid → cộng `agent_product_stock` + skip commission/trial (async, idempotent) | ✅ **DONE** |
| 5 | API đọc tồn kho agent (Hasura permission) | ✅ **DONE** (làm cùng P2) |
| 6 | Docs + catalog + smoke test end-to-end | ☐ TODO |

---

## ✅ Phase 1 — Field `resell_type` + CRM sync (DONE)

> Commit `62737109` + follow-up. Để tham chiếu, không làm lại.

- `ReferralOrder`: consts `RESELL_TYPE_*`, `RESELL_TYPES`, `RESELL_TYPES_SUPPORTED`, field `resellType` (varchar 32, nullable, index `idx_referral_order_resell_type`), `getEffectiveResellType()`.
- Migration `Version20260617072022` (ADD `resell_type` + index) — đã chạy.
- Create `Input` (`Assert\Choice(RESELL_TYPES_SUPPORTED)`) + `Resolver` + `Service::create()` (default `sell_via_crm`) + `ReferralOrderEntityType` expose.
- Hasura remote schema permission SDL (3 role) + `hasura:metadata:apply`.
- CRM sync: `Crm/UpdateReferralOrder.graphql` + `referralOrderPaidNoti(resellType)` + chain `OrderPaidMessage`/handler/subscriber truyền `resell_type`.

**Verified:** smoke test create (default/explicit/invalid) qua Hasura + Symfony OK; `RESELL_TYPES_SUPPORTED` chặn `sell_from_inventory`.

---

## ✅ Phase 2 — Cấu trúc DB: `agent_product_stock` + `stock_imported_at` (DONE)

**Đã làm:**
- `app/src/Entity/Stock/AgentProductStock.php` — id UUID v7, `agent` ManyToOne `User` (onDelete CASCADE, NOT NULL), `productId` string, `quantity` int (default 0), `createdAt`/`updatedAt` Timestampable; unique `(agent_id, product_id)` + **index `idx_agent_product_stock_product_id`**.
- `app/src/Repository/Stock/AgentProductStockRepository.php` — khai báo (logic `upsertIncrement` để ở P4).
- `app/src/Entity/ReferralOrder/ReferralOrder.php` — `stockImportedAt` (datetime nullable) + getter/setter + helper `isPurchaseToInventory()`.
- Migration `Version20260618071118` (CREATE TABLE + 3 index + FK CASCADE) + `Version20260618084930` (ADD `stock_imported_at`) — đã chạy, dọn drift `hdb_catalog`.
- Hasura: track `agent_product_stock` + select permission (ROLE_USER filter `agent_id`, ROLE_HASURA_CRM) + remote relationship `crm_product` → CRM; metadata export ra yaml.

**Verified:**
- `\d agent_product_stock`: PK, unique `(agent_id, product_id)`, index `product_id`, FK CASCADE → user. `referral_order.stock_imported_at` tồn tại.
- `migrations:diff` re-diff sạch (mapping khớp DB).
- `check-generated-docs.sh` pass (regenerate `docs/erd.md`).

---

## ☐ Phase 3 — (Optional) Permission cho `purchase_to_inventory` — create flow KHÔNG đổi

> **Quyết định (Q&A vòng 3):** đơn `purchase_to_inventory` tạo **y hệt đơn thường** — KHÔNG strip-down. Vẫn client/company, shipping, e-sign, trừ CRM stock, tax/shipping, pay-now tùy chọn. `resell_type` đã lưu ở phase 1. → **Không cần sửa Input/Resolver/Service ở create.**

**Mục tiêu (chỉ nếu business yêu cầu):** giới hạn chỉ `isAgent()` mới tạo được đơn `purchase_to_inventory`.

**Scope:** `GraphQL/ReferralOrder/Mutation/Create/Resolver.php` — thêm guard: nếu `resellType === purchase_to_inventory && !currentUser->isAgent()` → `GraphQLException`.

**Done when:** user không phải agent tạo `purchase_to_inventory` → bị từ chối (nếu bật rule). Nếu business không cần → **skip phase này**.

**Verify:** smoke test create với token non-agent → reject; token agent → OK.

---

## ✅ Phase 4 — Paid → cộng `agent_product_stock` (async, idempotent) (DONE)

**Đã làm:**
- `Repository/Stock/AgentProductStockRepository::upsertIncrement(Uuid, string, int)` — raw SQL `INSERT ... ON CONFLICT (agent_id, product_id) DO UPDATE SET quantity = quantity + EXCLUDED.quantity` (atomic, id = `Uuid::v7()`).
- `Message/ReferralOrder/AddAgentProductStockMessage` (mới) — `AsyncMailMessageInterface`, payload `orderId`.
- `MessageHandler/ReferralOrder/AddAgentProductStockMessageHandler` (mới) — guard `isPurchaseToInventory` + `status===paid` + `stockImportedAt===null`; loop products (bỏ `isShippingProduct`/null) → `upsertIncrement`; set `stockImportedAt` + flush, bọc `wrapInTransaction`.
- `ReferralOrderPaidSubscriber` — nhánh `resell_type===purchase_to_inventory`: dispatch `AddAgentProductStockMessage` + `return` (bỏ `ChangeUserAmountMessage`); giữ `OrderPaidMessage` (CRM-noti/PDF/email).
- `TransactionUpdateHandler` — skip tạo `OrderCommission` khi `isPurchaseToInventory()`.
- `CreateTrialFromReferralOrderMessageHandler` — guard skip trial khi `isPurchaseToInventory()`.
- Regenerate `docs/async-messages.md` + `docs/erd.md` → `check-generated-docs.sh` pass.

**Verified (e2e local, worker restart để load code mới):**
- Tạo đơn `purchase_to_inventory` (qty 7) → `UPDATE status=paid` → Hasura `ReferralOrderPaid` → subscriber → worker → `agent_product_stock.quantity=7`, `stock_imported_at` set. ✅
- Re-fire paid (pending_payment→paid) → qty **vẫn 7** (idempotency guard). ✅
- `order_commission` = 0 cho đơn. ✅

> **Lưu ý:** commission-skip ở `TransactionUpdateHandler` chỉ chạy qua đường `TransactionUpdate` message (CRM webhook), không qua psql update — guard đã thêm ở code, cần test thêm khi có luồng transaction thật.

---

## ✅ Phase 5 — API đọc tồn kho agent (Hasura permission) (DONE — làm cùng P2)

**Đã làm:**
- Select permission `agent_product_stock`: ROLE_USER filter `agent_id = X-Hasura-User-Id` (chỉ thấy kho mình); ROLE_HASURA_CRM full.
- Remote relationship `crm_product` (join `product_id` → CRM remote schema) — bonus, lấy info sản phẩm cùng query.
- Metadata export ra `app/hasura/metadata/sources/default/tables/public_agent_product_stock.yaml`.

**Verified:** query `agent_product_stock { product_id quantity crm_product { id name code stock unit_price } }` với token phu_vo (ROLE_USER) → trả đúng kho của agent + join CRM product OK. Query/response mẫu: [output/tech-spec-for-fe.md §4.3](output/tech-spec-for-fe.md).

> **Lưu ý:** field join tên `crm_product` (trả mảng). Master agent xem kho agent con — chưa làm (mở rộng nếu cần).

---

## ☐ Phase 6 — Docs + catalog + smoke test end-to-end

**Mục tiêu:** Đồng bộ tài liệu, catalog, và verify toàn flow.

**Scope:**
- `docs/domains/referral-order.md` + `docs/business/referral-order.md` — thêm `resell_type`, flow `purchase_to_inventory`, bảng `agent_product_stock`.
- Regenerate `docs/async-messages.md` (`scripts/extract_async_messages.py`) — message mới.
- Smoke test end-to-end (script dev/local): create → pay → kho cộng.

**Steps:**
1. Cập nhật docs.
2. Chạy script regenerate catalog.
3. Chạy smoke test end-to-end; cập nhật `tasks/D8-397/phase*/smoke-test-*.sh` nếu cần.

**Done when:** docs phản ánh đúng feature; catalog cập nhật; smoke test end-to-end pass.

---

## Ngoài scope (ghi nhận, chưa làm)

- **Refund `paid → cancelled`**: trừ ngược `agent_product_stock` — chưa có cơ chế (giống refund CRM stock hiện tại). Mở phase riêng nếu business cần.
- **`sell_from_inventory`**: bán từ kho agent — chưa hỗ trợ, mới khai báo enum.
- **FE**: KHÔNG cần đổi payload create — đơn `purchase_to_inventory` gửi như đơn thường, chỉ thêm `resell_type`.

## Phụ thuộc / lưu ý xuyên suốt

- **CRM-noti vs skip-commission** (P4): đừng vô tình bỏ `referralOrderPaidNoti` khi skip commission.
- **Idempotency** (P4): 2 lớp — subscriber `oldStatus===paid` + handler `stock_imported_at` + upsert atomic.
- **Migration**: luôn `diff` rồi dọn drift Hasura (`hdb_catalog`); review qua `db-migration-safety`.
- **Hasura metadata** không sửa qua hook-protected path nếu hook bật lại — apply qua `hasura:metadata:apply`.
