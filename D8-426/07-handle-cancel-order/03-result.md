# D8-426 / 07 — Implementation Result: Refund credit khi cancel đơn đã paid

> Plan: [`02-plan.md`](02-plan.md). Yêu cầu & Q&A: [`01-description.md`](01-description.md).
> Ngày: 2026-07-02. Môi trường: local dev.

## 1. Tóm tắt
Đơn `purchase_to_inventory` **đã paid + đã thực trừ credit** (`credit_deducted_at != null`), khi bị **CRM cancel** (`paid → cancelled`) → cộng lại `credit_use` vào `User.credit` + ghi `CreditTransaction(type=add_credit_from_order_refund)`, idempotent qua cột mới `referral_order.credit_refunded_at`.

## 2. Thay đổi code

| File | Nội dung |
|---|---|
| `app/src/Entity/Credit/CreditTransaction.php` | Thêm const `TYPE_ADD_CREDIT_FROM_ORDER_REFUND` (`'add_credit_from_order_refund'`, 28 ký tự ≤ cột `length:32`) + map `DIRECTION_BY_TYPE`→`credit`. Đổi quan hệ `→ReferralOrder`: **OneToOne(inversedBy) → ManyToOne(inversedBy: 'creditTransactions')** (bỏ UNIQUE) |
| `app/src/Entity/ReferralOrder/ReferralOrder.php` | Inverse: `OneToOne creditTransaction` → **`OneToMany creditTransactions` (Collection)** + init trong constructor + `getCreditTransactions()`. Xóa `getCreditTransaction()/setCreditTransaction()` (không dùng đâu). Thêm cột + getter/setter **`creditRefundedAt`** |
| `app/migrations/Version20260702050843.php` (MỚI) | DROP unique index `uniq_5e1de3e15fcbc899` → CREATE index thường; `ALTER TABLE referral_order ADD credit_refunded_at`. Đã bỏ dòng noise `CREATE SCHEMA hdb_catalog` trong down() |
| `app/src/Service/Credit/CreditService.php` | Thêm `refundForCancelledOrder(ReferralOrder)` — claim atomic `credit_refunded_at` (guard `credit_deducted_at IS NOT NULL AND credit_refunded_at IS NULL`) → persist `CreditTransaction(add_credit_from_order_refund, completed, amount=credit_use)` → `+User.credit` atomic, tất cả trong 1 `wrapInTransaction`. Đối xứng `deductForPaidOrder` |
| `app/src/Message/Credit/RefundOrderCreditMessage.php` (MỚI) | Message `AsyncMailMessageInterface` (field `referralOrderId`) → route `async_common` |
| `app/src/MessageHandler/Credit/RefundOrderCreditMessageHandler.php` (MỚI) | Re-load order; re-check `isPurchaseToInventory() && status===CANCELLED && creditDeductedAt !== null`; gọi `CreditService::refundForCancelledOrder` |
| `app/src/EventSubscriber/Hasura/ReferralOrderMutationSubscriber.php` | Dispatch `RefundOrderCreditMessage` khi `oldStatus===PAID && newStatus===CANCELLED` (độc lập với `ReferralOrderUpdateStatusMessage` để refund retry không re-run stock-restore không idempotent) |
| `app/src/MessageHandler/Transaction/TransactionUpdateHandler.php` | Sửa comment lỗi thời (bỏ nhắc `getCreditTransaction()` đã xóa) |
| `docs/async-messages.md` | Regenerate |

## 3. Vì sao dispatch từ subscriber, không cắm vào `ReferralOrderUpdateStatusMessageHandler`
Handler đó gọi `updateCrmProductStock` (INCREASE stock) **không idempotent** — nếu gộp refund vào cùng message rồi retry sẽ cộng kho CRM 2 lần. Tách `RefundOrderCreditMessage` riêng → refund retry độc lập (idempotent qua claim `credit_refunded_at`), không đụng luồng restore stock.

## 4. Idempotency & race (R1) — as-built
- **Double-refund**: claim `credit_refunded_at IS NULL` (atomic UPDATE, `affected=1` mới hoàn) chặn redeliver/trigger lặp.
- **Refund đơn chưa từng trừ**: claim yêu cầu `credit_deducted_at IS NOT NULL` → skip.
- **Race cancel↔debit async (R1)**: `DeductOrderCreditMessageHandler` đã re-check `getStatus() === STATUS_PAID` (`:34`) → nếu đơn đã cancelled thì debit **skip**. Refund handler re-check `status === CANCELLED && creditDeductedAt !== null`. → Không có case trừ mà không hoàn / hoàn mà chưa trừ.

## 5. Verify đã chạy (✅ static + metadata)
- `composer dump-autoload` + `cache:clear` — DI container compile OK.
- `doctrine:migrations:migrate` — Version20260702050843 applied (1 migration, 3 sql).
- `doctrine:schema:validate` — **Mapping OK + Database in sync**.
- `debug:messenger` — `RefundOrderCreditMessage → RefundOrderCreditMessageHandler` registered.
- `php -l` — 6 file đổi/mới: PASS.
- Hasura `reload_metadata` + `get_inconsistent_metadata` — **is_consistent: true, inconsistent_objects: []** (R4 confirmed: drop unique không phá relationship nào; `referral_order` không có relationship ngược tới `credit_transaction`; object relationship phía `credit_transaction` qua FK vẫn hợp lệ khi ManyToOne).
- `docker compose restart worker` — worker load code handler mới.

## 6. Test end-to-end đã chạy (✅ 2026-07-02, local dev)

Full path thực: `UPDATE referral_order SET status=...` (SQL trực tiếp) → Hasura event trigger `ReferralOrderMutation` fire → `ReferralOrderMutationSubscriber` → dispatch `RefundOrderCreditMessage` → worker `messenger:consume async_common` → `RefundOrderCreditMessageHandler` → `CreditService::refundForCancelledOrder`.

Đơn test: **2047** (PTI, `phu_vo@fastboy.net`). Seed qua SQL: `paid`, `credit_use=30`, `credit_deducted_at=now()`, `user.credit=470` (giả lập đã trừ 30/500).

| # | Kịch bản | Kết quả | Bằng chứng |
|---|---|---|---|
| T1 | **Happy path**: paid → cancelled | ✅ | `user.credit` 470 → **500**; `credit_refunded_at` set; ledger `add_credit_from_order_refund / credit / completed / 30` |
| T2 | **Idempotency + R1**: re-fire `paid → cancelled` lần 2 | ✅ | `user.credit` VẪN **500**; **1** row refund (không tạo row 2); KHÔNG có row `use_credit_for_order` mới → deduct cũng bị chặn bởi `credit_deducted_at` khi đi qua paid |
| T3 | **Đơn chưa trừ** (đơn 2045: `paid`, `credit_use=20`, `credit_deducted_at=NULL`) → cancelled | ✅ | `user.credit` giữ **500**; `credit_refunded_at`=NULL (claim skip); **0** row refund — guard `credit_deducted_at IS NOT NULL` chặn đúng |
| T4 | **==0 (credit phủ hết)** | ✅ (gián tiếp) | Logic refund khi cancel không phân biệt ==0/>0 — chỉ đọc `credit_use` + `credit_deducted_at`; đã chứng minh ở T1 (chỉ khác giá trị) |

**Kết luận:** luồng refund credit (trigger → subscriber → message → handler → service, atomic + idempotent + guard chưa-trừ) hoạt động đúng end-to-end.

## 6b. Dữ liệu test để lại (dev — user chọn không cleanup)
- Đơn 2047: `cancelled`, credit_use=30, credit_deducted_at + credit_refunded_at set, 1 ledger row refund.
- Đơn 2045: `cancelled`, credit_use=20, credit_deducted_at NULL, không refund.
- `phu_vo@fastboy.net`.credit = 500.
- **Side effect**: khi flip 2047 qua `paid` ở T2, `ReferralOrderPaidSubscriber` (PTI) đã dispatch `AddAgentProductStockMessage` + `OrderPaidMessage` → có thể đã cộng kho agent + sync CRM cho đơn 2047. Cần dọn thủ công nếu muốn.

## 6c. CHƯA làm / cần làm tiếp
- Commit code feature.

## 7. Out of scope (giữ nguyên)
- Partial cancel / partial refund.
- Refund khi `client_rejected` / void path khác sau paid.
- Refund cho resell_type khác PTI.
- Release ngầm cho đơn CHƯA paid bị cancel (đã có, không đụng).
