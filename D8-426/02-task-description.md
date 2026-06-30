# Task D8-426: Thanh toán đơn hàng bằng Deposit Balance (credit)

> Bản mở rộng của [`01-task-description.md`](01-task-description.md) — bổ sung kết quả Q&A, các quyết định đã chốt, và rủi ro đang chờ confirm.
> Cập nhật: 2026-06-30 (rev 3 — sau khi rebase infra credit từ develop; chuyển sang mô hình **reserve-via-status**).

> ⚠️ **Thay đổi lớn (rev 3):** Hạ tầng credit (`user.credit`, bảng `credit_transaction`, inflow/top-up qua `RESELL_TYPE_DEPOSIT`) **đã được team khác build** và rebase về develop — xem [`docs/domains/credit.md`](../../docs/domains/credit.md) + [ADR-0005](../../docs/adr/0005-reseller-credit-balance-via-topup-order.md). Task này = **DEBIT/spend phase** (tiêu credit cho đơn PTI). Mô hình hold đã đổi: **KHÔNG** hold qua `credit_transaction` (bảng không có cột `status`), mà **hold ngầm qua `referral_order.status`**. Chi tiết kỹ thuật ở [`03-plan.md`](03-plan.md).

## 1. Mục tiêu

Cho phép user dùng **credit** (deposit balance) trừ trực tiếp vào tiền order. User nhập số tiền muốn dùng (`credit_use`); phần còn lại charge qua payment gateway.

- Order total = $100, `user.credit` = $30 → user nhập `credit_use` tối đa $30, còn $70 charge qua gateway.
- **Chỉ áp dụng khi `resell_type = purchase_to_inventory`** (PTI — agent mua hàng từ Fastboy về nhập kho).
- Phần **nạp credit (top-up) CHƯA làm** đợt này — xử lý sau. Hệ quả: `user.credit` thực tế luôn = 0 cho tới khi có top-up → muốn test phải seed thủ công.

## 2. Phạm vi đợt này (scope)

**Làm:**
- Thêm field `referral_order.credit_use` (float). *(`user.credit` + `credit_transaction` đã có sẵn — không tạo lại.)*
- Cho nhập `credit_use` ở mutation create/update/preview order (chỉ PTI), validate `≤ min(gross, available)`.
- Trả `totalAfterTax` (đã trừ credit) về FE để show; gateway charge phần còn lại.
- **Reserve ngầm qua status**: đơn có `credit_use>0` ở reserve-status (draft/sent/viewed/signed/pending_payment/decline_payment) thì giữ credit; `available = user.credit − SUM(credit_use các đơn reserve)`.
- Pay now:
  - `totalAfterTax == 0` → mark order paid + trừ `user.credit` ngay + tạo `CreditTransaction(DEBIT)`.
  - `totalAfterTax > 0` → FE charge phần còn lại qua gateway.
- Listen order paid event từ CRM (nhánh >0) → trừ `user.credit` atomic + tạo `CreditTransaction(DEBIT)` (idempotent qua OneToOne UNIQUE).
- **Release = ngầm**: đơn `cancelled`/`client_rejected` tự rớt khỏi reserve-status → credit thả, không cần code. `decline_payment` vẫn giữ (cho retry).

**KHÔNG làm đợt này:**
- Top-up / cộng credit (đã có, team khác).
- Refund/reverse credit khi đơn **đã paid** bị void/cancel (ADR-0005: phantom balance, phase sau).
- Thêm cột `status` cho `credit_transaction` (cố ý không có).

## 3. Quyết định đã chốt (Q&A với codeowner)

| # | Câu hỏi | Quyết định |
|---|---------|-----------|
| 1 | Xử lý `totalAfterTax` thế nào khi áp credit? | **Trừ thẳng vào `totalAfterTax`**: `totalAfterTax = gross - credit_use`. Gross gốc tái tạo được = `totalAfterTax + credit_use`. |
| 2 | ~~Lưu khoản "hold" ở đâu?~~ (rev 3 OVERRIDE) | **Hold ngầm qua `referral_order.status`**, KHÔNG dùng `credit_transaction` để hold. `credit_transaction` (đã có, không status) chỉ là **bút toán DEBIT + idempotency key**, tạo tại thời điểm paid. |
| 3 | Khi nào reserve? (rev 3 OVERRIDE) | **Reserve ngay khi tạo đơn có `credit_use>0`** (bất kể pay-now). Reserve = đơn ở reserve-status. Deduct tại paid. Release ngầm khi rời reserve-status. |
| 4 | Phạm vi hoàn/giải phóng credit? | **Release ngầm** (cancel/reject tự thả). Decline giữ cho retry (D3). **Defer** refund đơn đã paid (ADR-0005). |
| Q1 | Reserve-status set | draft, sent, viewed, signed, pending_payment, decline_payment (giữ); paid/cancelled/client_rejected (không). |
| Q2 | Công thức available | `user.credit − SUM(credit_use đơn ở reserve-status)` (chấp nhận khe ngắn paid→debit async). |
| Q4 | Overdraft tại paid | Atomic guard `WHERE credit >= amount` + clamp về 0 + log → credit không âm. |
| Q5 | transId của DEBIT | gateway trans (nhánh >0); `null` (nhánh ==0). |

## 4. Luồng nghiệp vụ chi tiết (theo quyết định trên)

```
CREATE / UPDATE order (chỉ purchase_to_inventory), credit_use > 0
  └─ validate: PTI + credit_use ≤ gross + credit_use ≤ available
  └─ totalAfterTax = gross - credit_use   (lưu vào referral_order)
  └─ RESERVE = ngầm: đơn vào reserve-status, credit_use giữ chỗ trong available

PAY NOW (submit)
  ├─ totalAfterTax == 0  (credit phủ hết)
  │     └─ resolver set PAID trực tiếp (mark-paid riêng cho PTI, bypass transition table)
  │     └─ deductForPaidOrder(transId=null): -user.credit atomic + CreditTransaction(DEBIT)
  │     └─ (nếu là create/INSERT) dispatch thủ công AddAgentProductStock + OrderPaid
  │
  └─ totalAfterTax > 0
        └─ FE redirect gateway charge phần totalAfterTax còn lại

CRM ORDER PAID EVENT (gateway settle) — nhánh >0
  └─ order → PAID  (TransactionUpdateHandler)
  └─ ReferralOrderPaidSubscriber nhánh PTI → dispatch DeductOrderCreditMessage
        └─ handler: re-check PAID+PTI → deductForPaidOrder(transId): -user.credit atomic + CreditTransaction(DEBIT)
        └─ idempotent qua getCreditTransaction() !== null (OneToOne UNIQUE)

CANCELLED / CLIENT_REJECTED
  └─ đơn rớt khỏi reserve-status → SUM available không tính → credit thả (KHÔNG code)
DECLINE_PAYMENT
  └─ vẫn reserve (giữ credit cho retry)
```

### Điểm móc nối trong code hiện tại
- Hook trừ credit (nhánh >0): `app/src/EventSubscriber/Hasura/ReferralOrderPaidSubscriber.php` nhánh `RESELL_TYPE_PURCHASE_TO_INVENTORY` (**line 112** — cạnh `AddAgentProductStockMessage`).
- Khuôn handler debit: mirror `app/src/MessageHandler/Credit/CreditResellerBalanceMessageHandler.php` (atomic UPDATE, guard OneToOne, `wrapInTransaction`).
- PTI **không tạo commission**: `TransactionUpdateHandler.php:80-82` skip khi `isPurchaseToInventory()`/`isDeposit()`.
- `==0` không có CRM transaction → mark-paid bằng path riêng ở resolver (trigger `ReferralOrderPaid` chỉ fire UPDATE, không INSERT).

## 5. Rủi ro / điểm chờ confirm

### ✅ R1 (ĐÃ GỠ) — CRM tính số tiền charge từ field `total_after_tax`
App **không** đẩy số tiền charge sang CRM. `UpdateReferralOrder.graphql` chỉ gửi `id / internal_id / status / resell_type` (mutation `crm_payment_resell_update_order_status`); `UpdateCrmOrderMessageHandler` chỉ *nhận* `crmInternalId` về. Việc dựng `fin_invoice` + charge nằm **hoàn toàn bên CRM** (không có trong repo này).

**Xác nhận từ team CRM (2026-06-30): CRM tính số tiền charge theo field `total_after_tax`.**
→ Đây là **trường hợp (A)** → choice "trừ thẳng `totalAfterTax`" **chạy đúng**: giảm `total_after_tax` thì gateway charge đúng phần còn lại. **Không còn blocker.**

> Lưu ý kéo theo: vì charge bám `total_after_tax`, mọi nơi ghi đè/recompute `total_after_tax` (vd update order, recompute tax/shipping) **phải luôn áp `credit_use`** để không vô tình charge full. Đảm bảo `total_after_tax` lưu xuống DB luôn là giá trị *sau khi trừ credit*.

### ✅ R2 (ĐÃ CHỐT) — Credit của người tạo đơn
Credit dùng để trừ = `user.credit` của **người tạo đơn** (`referralOrder.getCreatedBy()`), đúng với bản chất PTI (agent mua hàng nhập kho). Xác nhận 2026-06-30.

### R3 — `totalAfterTax == 0` cần mark-paid riêng cho PTI
`MarkPaid` hiện guard cứng chỉ cho `sell_from_inventory` (`MarkPaid/Resolver.php:54`, ADR-0002). Nhánh credit phủ hết của PTI → set `paid` ngay trong Create/Update resolver (bypass transition table, guard PTI+pay-now+total==0+owner) + dispatch downstream thủ công nếu là create/INSERT (trigger không fire INSERT).

### R4 — Idempotency double-deduct
Khóa = `credit_transaction` OneToOne UNIQUE theo order: `getCreditTransaction() !== null` → skip. Chặn cả khi subscriber fire nhiều lần, hay cả resolver(==0) lẫn subscriber(update→paid) cùng chạy.

### R5 — Re-validate khi update
`credit_use ≤ min(gross, available)`. User sửa product làm total tụt dưới `credit_use` → `applyCrmProductData` chạy lại mỗi update → tự throw. Lưu `credit_use` để recompute gross (vì `totalAfterTax` đã trừ).

### R6 — Overdraft + concurrency (Q4)
Reserve-via-status giữ credit suốt đời đơn nên tại paid thường đủ. Vẫn để atomic guard `WHERE credit >= amount` + clamp về 0 + log làm lưới (credit không âm). Khe hiếm: 2 create song song cùng đọc available → over-reserve; phase 1 chấp nhận.

### R7 — Testing
Seed credit qua DepositOrder thật (`resell_type=deposit`, đã build) hoặc SQL `UPDATE user SET credit=...`. Test 2 nhánh (==0 / >0) + cancel (thả) + decline→retry (giữ→trừ).

## 6. Việc cần làm trước khi code
1. ~~R1 CRM charge~~ ✅ theo `total_after_tax`.
2. ~~R2 credit của ai~~ ✅ người tạo đơn (`getCreatedBy()`).
3. ~~Cấu trúc hold~~ ✅ reserve-via-status (rev 3); `credit_transaction` đã có (không status).
→ Đủ điều kiện code. Chi tiết: [`03-plan.md`](03-plan.md).

## Tài liệu tham khảo
- [`01-task-description.md`](01-task-description.md) — mô tả gốc
- [`03-plan.md`](03-plan.md) — implementation plan (rev 3)
- `docs/domains/credit.md` — domain Credit (inflow đã build)
- `docs/adr/0005-...md` — ADR credit balance (DEBIT = phase này)
- `docs/domains/referral-order.md`, `docs/adr/0002-...md` — ReferralOrder status flow / MarkPaid
