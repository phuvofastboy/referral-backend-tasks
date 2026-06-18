# D8-397 — Implementation Plan

**Feature:** Agent Purchase Products & nhập kho riêng của Agent (`resell_type`)
**Tài liệu:** [01-requirement.md](01-requirement.md) · [output/tech-spec.md](output/tech-spec.md) · [output/tech-spec-for-fe.md](output/tech-spec-for-fe.md)

Mỗi phase = 1 deliverable cụ thể, tự hoàn thành & verify được. Làm tuần tự P2 → P6.

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 1 | Field `resell_type` + CRM sync | ✅ **DONE** |
| 2 | Cấu trúc DB: bảng `agent_product_stock` + `stock_imported_at` | ☐ TODO (bắt đầu) |
| 3 | Strip-down flow tạo đơn `purchase_to_inventory` | ☐ TODO |
| 4 | Paid → cộng `agent_product_stock` (async, idempotent) | ☐ TODO |
| 5 | API đọc tồn kho agent (Hasura permission) | ☐ TODO |
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

## ☐ Phase 2 — Cấu trúc DB: `agent_product_stock` + `stock_imported_at`

**Mục tiêu:** Tạo schema cho kho agent + cờ guard idempotency. Chưa có logic.

**Scope:**
- `app/src/Entity/Stock/AgentProductStock.php` (mới) — id UUID v7, `agent` ManyToOne `User` (onDelete CASCADE, NOT NULL), `productId` string, `quantity` int, `createdAt`/`updatedAt` Timestampable; `#[ORM\UniqueConstraint(name: 'uniq_agent_product_stock_agent_product', columns: ['agent_id','product_id'])]`.
- `app/src/Repository/Stock/AgentProductStockRepository.php` (mới) — khai báo (logic `upsertIncrement` ở P4).
- `app/src/Entity/ReferralOrder/ReferralOrder.php` — thêm `stockImportedAt` (datetime nullable) + getter/setter + helper `isPurchaseToInventory()`.

**Steps:**
1. Viết entity + repository + sửa ReferralOrder.
2. `php bin/console doctrine:migrations:diff` → dọn drift (`CREATE SCHEMA hdb_catalog`).
3. Review migration (skill `db-migration-safety`).
4. `doctrine:migrations:migrate`.
5. Hasura: track bảng `agent_product_stock` + `hasura:metadata:apply`.

**Done when:**
- `agent_product_stock` tồn tại với unique `(agent_id, product_id)`; `referral_order.stock_imported_at` tồn tại.
- `php bin/console doctrine:schema:validate` mapping OK.
- Bảng được track trên Hasura console.

**Verify:** `psql` kiểm tra table + cột + unique constraint; `cache:clear` không lỗi.

---

## ☐ Phase 3 — Strip-down flow tạo đơn `purchase_to_inventory`

**Mục tiêu:** Tạo đơn `purchase_to_inventory` bỏ client/shipping/e-sign/commission/trial, force pay-now.

**Scope:**
- `GraphQL/ReferralOrder/Mutation/Create/Input.php` — GroupSequence group `onPurchaseToInventory` (kích hoạt khi `resellType === purchase_to_inventory`): chỉ validate `products`; bỏ XOR client/company, shipping, trial. (Tùy chọn) `Assert\Callback` reject nếu gửi kèm company/shipping.
- `GraphQL/ReferralOrder/Mutation/Create/Resolver.php` — rẽ nhánh: check `isAgent()`, bỏ resolve company/clientInfo/shippingAddress, bỏ createReferral/createTrial, force `isPayNow=true`, **không** tạo `ReferralOrderDocument`.
- `Service/ReferralOrder/ReferralOrderService.php` — `create()` + `applyCrmProductData()`: khi `isPurchaseToInventory()` → bỏ snapshot commission (`:481-482`), bỏ `updateCrmProductStock()` (không trừ CRM), vẫn lấy insider price.

**Steps:**
1. Sửa Input GroupSequence.
2. Sửa Resolver rẽ nhánh strip-down.
3. Sửa Service `create`/`applyCrmProductData`.
4. `cache:clear`.

**Done when:**
- Tạo đơn `purchase_to_inventory` với chỉ `products` (không company/shipping) → thành công, `is_pay_now=true`, không có document/commission, CRM stock không đổi.
- User không phải agent → `Permission denied`.

**Verify:** smoke test create (token agent phu_vo) — đơn strip-down tạo OK; check DB: order không có document, `creator_commission_amount` null; CRM stock không giảm.

---

## ☐ Phase 4 — Paid → cộng `agent_product_stock` (async, idempotent)

**Mục tiêu:** Khi đơn `purchase_to_inventory` paid → cộng kho 1 lần; bỏ commission/trial, giữ CRM-noti/PDF/email.

**Scope:**
- `Repository/Stock/AgentProductStockRepository.php` — `upsertIncrement(Uuid $agentId, string $productId, int $quantity)` raw SQL `INSERT ... ON CONFLICT (agent_id, product_id) DO UPDATE SET quantity = quantity + EXCLUDED.quantity, updated_at = now()`.
- `app/src/Message/ReferralOrder/AddAgentProductStockMessage.php` (mới) — `AsyncMailMessageInterface`, payload `orderId`.
- `app/src/MessageHandler/ReferralOrder/AddAgentProductStockMessageHandler.php` (mới) — guard `isPurchaseToInventory` + `stockImportedAt===null` + `status===paid`; loop products (bỏ `isShippingProduct`) → `upsertIncrement`; set `stockImportedAt` + flush (atomic).
- `EventSubscriber/Hasura/ReferralOrderPaidSubscriber.php` — nhánh `resell_type===purchase_to_inventory`: dispatch `AddAgentProductStockMessage`; **bỏ** `ChangeUserAmountMessage` (commission); **giữ** `referralOrderPaidNoti`/PDF/email.

**Steps:**
1. Implement `upsertIncrement`.
2. Message + handler.
3. Sửa subscriber rẽ nhánh (cẩn thận giữ CRM-noti khi bỏ commission).
4. Đăng ký route message nếu cần; `cache:clear`.

**Done when:**
- Đơn `purchase_to_inventory` chuyển `paid` → `agent_product_stock.quantity` cộng đúng số lượng; `stock_imported_at` được set.
- Gửi duplicate event / retry message → quantity **không** cộng lần 2.
- Không tạo `OrderCommission`/`ChangeUserAmount`; `referralOrderPaidNoti` (CRM) vẫn chạy.

**Verify:** local — set order paid (qua `referral_order_update_status_by_transaction_mutation` hoặc trigger), `messenger:consume`, check `agent_product_stock`; chạy lại message → không đổi.

---

## ☐ Phase 5 — API đọc tồn kho agent (Hasura permission)

**Mục tiêu:** FE query được tồn kho của chính agent (`agent_product_stock`).

**Scope:**
- Hasura metadata: select permission cho `agent_product_stock` theo role `ROLE_USER` — filter `agent_id = X-Hasura-User-Id` (agent chỉ thấy kho mình); cân nhắc master agent xem kho agent con.
- `hasura:metadata:apply`.

**Steps:**
1. Thêm select permission (columns: id, agent_id, product_id, quantity, created_at, updated_at; filter theo user).
2. Apply metadata.

**Done when:**
- Query `agent_product_stock` với token agent → trả đúng kho của agent đó, không thấy kho người khác.

**Verify:** smoke test query `agent_product_stock` với token phu_vo (sau khi P4 đã cộng kho) → trả đúng row.

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
- **FE đồng bộ strip-down**: FE bỏ `company`/`shipping_address` khi `purchase_to_inventory` (phối hợp team FE).

## Phụ thuộc / lưu ý xuyên suốt

- **CRM-noti vs skip-commission** (P4): đừng vô tình bỏ `referralOrderPaidNoti` khi skip commission.
- **Idempotency** (P4): 2 lớp — subscriber `oldStatus===paid` + handler `stock_imported_at` + upsert atomic.
- **Migration**: luôn `diff` rồi dọn drift Hasura (`hdb_catalog`); review qua `db-migration-safety`.
- **Hasura metadata** không sửa qua hook-protected path nếu hook bật lại — apply qua `hasura:metadata:apply`.
