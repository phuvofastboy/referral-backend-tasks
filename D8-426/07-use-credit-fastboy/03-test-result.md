# D8-426/07 — Test Result: use credit cho `sell_via_crm` (sell_via_fastboy)

> Verify theo [`02-plan.md`](02-plan.md) §5. Ngày 2026-07-01. Môi trường: local dev (https://localhost/graphql), reseller test = `phu_vo@fastboy.net` (agent, credit khởi điểm 500), order `#2048` (sell_via_crm, gross total_after_tax=40, product `Device_Duyen1`).
> Token JWT tự dựng theo skill `smoke-test-graphql-api` (iss=https://localhost, sub=email).

## 1. Static checks — ✅
- `php -l` 6 file đổi (Entity, Service, Subscriber, Handler, Create/Update resolver): PASS.
- `doctrine:schema:validate`: mapping OK, DB in sync (task không đổi schema).
- `graphqlite:dump-schema`: `credit_use` xuất hiện 4× (không đổi — không thêm field).

## 2. Runtime E2E

| # | Test | Cách | Kỳ vọng | Kết quả |
|---|---|---|---|---|
| A | Gate mở + trừ total | Preview `sell_via_crm` + `credit_use=10` | `total_after_tax = 40→30`, không lỗi gate | ✅ `total_after_tax=30` |
| B1 | Cap ≤ gross | Preview `credit_use=9999` | `"credit_use exceeds order total"` | ✅ đúng message |
| B2 | Baseline | Preview `credit_use=0` | `total_after_tax=40` | ✅ |
| B3 | Gate reject type | Preview `sell_from_inventory` + `credit_use=10` | `"Credit can only be used for purchase_to_inventory or sell_via_crm orders"` | ✅ đúng message |
| B4 | PTI không regress | Preview `purchase_to_inventory` + `credit_use=10` | Qua gate credit (bị chặn sau ở assertResellerInventoryCompany, lý do khác) | ✅ không dính gate credit |
| C | Deduct khi paid | Set order `signed`+`credit_use=10`, `UPDATE status='paid'` (fire trigger) → subscriber → handler | credit `500→490`; `credit_transaction(amount=10, debit, use_credit_for_order, completed)`; `credit_deducted_at` set | ✅ tất cả đúng |
| D | Idempotency | Re-fire `signed→paid` lần 2 | credit vẫn `490`, vẫn `1` credit_transaction | ✅ không trừ đôi |
| E | Commission add-back | Submit `sent` (không pay-now): baseline vs `credit_use=10` | baseline commission=0 (agent, no rate); `credit_use=10` → commission = base+10 = `10`, total_after_tax=30 | ✅ `creator_commission_amount=10` |
| F | Full-cover mark-paid | Submit `credit_use=40` (=gross), pay-now | status=`paid` ngay; commission = base+40 = `40`; credit `490→450`; `credit_transaction(40, debit, use_credit_for_order, completed)`; **không** nhập agent stock | ✅ paid + deduct + add-back; AddAgentProductStock bị gate PTI trong subscriber nên sell_via_crm không nhập kho |

## 3. Ghi chú về phương pháp verify

- **Test A/B (gate, total, validation)** chạy qua `referral_order_preview_order` — preview gọi service **không** truyền status ⇒ `$isSubmit=false`, nên `applyCreditUse` (gate + trừ total + validate) exercise được, còn commission add-back (guard `$isSubmit`) thì không.
- **Test E/F (commission add-back + submit)** cần `$isSubmit=true`. Submit trên local bị chặn bởi **CRM stock check** của product test (`CrmProductConstraintValidator` "out of stock" + `queryCheckProductStock` "inactive or out of stock") — lỗi dữ liệu CRM mock, không liên quan code task. Đã **tạm skip 2 validation này** (`CrmProductConstraintValidator::$skipOutOfStockCheck=true`; `ReferralOrderService` bỏ `validateProductShipping` + `queryCheckProductStock($changeSet, false)`) để submit chạy, verify xong đã **revert đầy đủ** (grep `TEMP D8-426/07` = NONE; lint lại PASS).
- **Test C/D (deduct + idempotency)** mô phỏng CRM paid bằng `UPDATE referral_order SET status='paid'` — Hasura event trigger fire trên raw SQL → `ReferralOrderPaidSubscriber` → `DeductOrderCreditMessage` → worker (`async_common`).

## 4. Kết luận

Tất cả nhánh trong plan đã verify runtime PASS:
- Gate credit mở cho `sell_via_crm`, giữ PTI, reject type khác (§2.2, 2.5 plan).
- `credit_use` trừ `total_after_tax`; validate ≤ gross (≤ available là code chung PTI, đã verify ở D8-426 trước).
- Commission add-back `creatorCommissionAmount = base + credit_use` lúc submit (§2.3).
- Deduct `user.credit` + ghi `credit_transaction(debit, use_credit_for_order)` khi paid, idempotent (§2.4, 2.5).
- Full-cover → mark PAID ngay, **không** AddAgentProductStock cho sell_via_crm (§2.6).

## 5. Lưu ý dữ liệu (chưa restore theo yêu cầu)
- Order `#2048`: status=`paid`, `credit_use=40`, `creator_commission_amount=40`, có `credit_transaction` debit.
- `phu_vo@fastboy.net`: `credit=450` (từ 500, đã trừ 40 ở Test F; Test C/D trừ rồi được ghi đè bởi reset giữa các test).
