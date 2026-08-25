# Task: Handle cancel order

## Description
- Sau khi order paid (Referral), order này vẫn có thể được cancel (từ CRM)
- Cần bổ sung requirement, nếu order đã được khấu trừ credit, sau đó bị cancel thì cần phải
  - Cộng lại credit cho user
  - Tạo transaction log để ghi nhận lại, và có type mới gọi là add_credit_from_order_refund
- Q&A để clear requirement

## Q&A

> Chốt qua Q&A với codeowner (2026-07-02). Đây là phần **refund/reverse credit** đã bị cố ý defer ở D8-426 DEBIT phase (ADR-0005 "phantom balance, phase sau").

### Bối cảnh đã xác định trong code
- Đơn PTI paid đã trừ credit thật: `−User.credit` + `CreditTransaction(type=use_credit_for_order)` + set `referral_order.credit_deducted_at` (xem `CreditService::deductForPaidOrder`).
- Cancel từ CRM đi qua: trigger Hasura `ReferralOrderMutation` → `ReferralOrderMutationSubscriber` → `ReferralOrderUpdateStatusMessage` (mang `changeSet` old/new status) → `ReferralOrderUpdateStatusMessageHandler` (đã có sẵn nhánh `STATUS_CANCELLED`/`STATUS_CLIENT_REJECTED` để hoàn kho CRM). → **Đây là điểm móc nối tự nhiên** vì handler biết được transition `paid → cancelled`.
- Điều kiện "đã thực trừ credit" = `credit_use > 0 AND credit_deducted_at IS NOT NULL`.

### ⚠️ Blocker kỹ thuật cốt lõi
`CreditTransaction → ReferralOrder` hiện là **`OneToOne` + `JoinColumn(nullable:false)` UNIQUE** (`CreditTransaction.php:74-76`; inverse `ReferralOrder.php:288` `OneToOne(mappedBy)`). Đơn paid đã có 1 row `use_credit_for_order` → ghi thêm row `add_credit_from_order_refund` cho cùng order sẽ **vi phạm UNIQUE**.

### Quyết định đã chốt

| # | Câu hỏi | Quyết định |
|---|---------|-----------|
| 1 | Xử lý ràng buộc OneToOne UNIQUE thế nào để ghi được row refund? | **Đổi `CreditTransaction→ReferralOrder` sang `ManyToOne`** (bỏ UNIQUE): 1 order có nhiều ledger row (debit + refund). Inverse `ReferralOrder.creditTransaction` (OneToOne) → đổi thành `OneToMany` collection `creditTransactions`. Debit idempotency đã chuyển sang `credit_deducted_at` (rev 4) nên KHÔNG còn phụ thuộc UNIQUE này. Cần migration drop unique index. |
| 2 | Status nào (từ CRM) kích hoạt refund cho đơn đã paid + đã trừ credit? | **Chỉ `cancelled`** (đúng mô tả task). Transition `paid → cancelled`. Không đụng `client_rejected` hay status khác đợt này. |
| 3 | Idempotency chống hoàn nhiều lần (redeliver / trigger lặp)? | **Thêm cột `referral_order.credit_refunded_at`** (đối xứng `credit_deducted_at`). Claim atomic: `UPDATE referral_order SET credit_refunded_at=now() WHERE id=:id AND credit_use>0 AND credit_deducted_at IS NOT NULL AND credit_refunded_at IS NULL` — `affected=1` mới hoàn. |
| 4 | Số tiền hoàn? | **Full `credit_use`** (debit luôn trừ full `credit_use`). Chỉ hoàn khi `credit_deducted_at IS NOT NULL`. **Không** xử lý partial cancel đợt này. |

### Luồng nghiệp vụ đã chốt
```
Đơn PTI đã paid (credit_use>0, credit_deducted_at != null)
  └─ CRM cancel → status paid → cancelled
       └─ ReferralOrderMutation trigger → ReferralOrderUpdateStatusMessage (old=paid, new=cancelled)
       └─ [MỚI] refund credit:
            claim atomic credit_refunded_at (idempotent)  →  affected=1 mới làm tiếp
            +User.credit atomic (round, allow lên lại)
            ghi CreditTransaction(type=add_credit_from_order_refund, direction=credit, amount=credit_use)
            (tất cả trong 1 transaction, đối xứng deductForPaidOrder)

Đơn PTI CHƯA paid bị cancel → credit release ngầm qua status (đã có, KHÔNG code) — giữ nguyên.
```

### Việc kéo theo (chưa chốt chi tiết — sẽ vào plan)
- `CreditTransaction::TYPE_ADD_CREDIT_FROM_ORDER_REFUND = 'add_credit_from_order_refund'` + map `DIRECTION_BY_TYPE` → `credit`.
- `CreditService::refundForCancelledOrder(ReferralOrder $order): void` (đối xứng `deductForPaidOrder`).
- Entity: field `creditRefundedAt` + đổi quan hệ OneToOne→ManyToOne/OneToMany.
- Migration: `ADD referral_order.credit_refunded_at` + DROP unique index trên `credit_transaction.referral_order_id`.
- Cắm refund vào nhánh `STATUS_CANCELLED` của `ReferralOrderUpdateStatusMessageHandler` (chỉ khi `paid → cancelled` + đã trừ credit). Cân nhắc: message riêng `RefundOrderCreditMessage` hay gọi trực tiếp trong handler async đã có.
- **Out of scope** (giữ nguyên): partial cancel, `client_rejected` sau paid, refund cho các resell_type khác.
