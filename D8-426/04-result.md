# D8-426 — Implementation Result

> Kết quả implement DEBIT phase (tiêu credit cho đơn `purchase_to_inventory`).
> Plan: [`03-plan.md`](03-plan.md) (rev 3 — reserve-via-status). Yêu cầu: [`02-task-description.md`](02-task-description.md).
> Ngày: 2026-06-30. Commit chứa code: `c38a8556 [D8-426] tmp`.

## 1. Tóm tắt

Cho phép reseller dùng `User.credit` (deposit balance, inflow đã có sẵn từ feature reseller-credit-topup) để trừ vào tổng đơn `purchase_to_inventory`:
- Nhập `credit_use` ở create/update/preview → `totalAfterTax = gross − credit_use` (gateway charge phần còn lại; CRM charge bám `total_after_tax`).
- **Hold ngầm qua `referral_order.status`** (reserve-set), KHÔNG dùng `credit_transaction` để hold.
- Trừ thật vào `User.credit` + ghi `CreditTransaction(direction=DEBIT)` **khi đơn paid** (atomic, idempotent).
- `totalAfterTax == 0` (credit phủ hết) → mark paid ngay, không qua gateway.

## 2. Mô hình (đã chốt qua Q&A)

| Khía cạnh | Quyết định |
|---|---|
| Lưu `credit_use` | Cột `referral_order.credit_use` (float, nullable) |
| Giảm tiền | `totalAfterTax = gross − credit_use` (trừ thẳng) |
| Credit của ai | Người tạo đơn (`getCreatedBy()`) |
| Hold | Ngầm qua `referral_order.status` (reserve-set: draft/sent/viewed/signed/pending_payment/decline_payment) |
| available | `User.credit − SUM(credit_use đơn ở reserve-status)` |
| Reserve khi nào | Mọi đơn `credit_use>0` (bất kể pay-now) |
| Deduct khi nào | Tại paid: claim `referral_order.credit_deducted_at` + `−User.credit` atomic |
| Idempotency | Cột `referral_order.credit_deducted_at` — claim atomic `UPDATE ... WHERE credit_deducted_at IS NULL` (giữ), + OneToOne UNIQUE của `credit_transaction` làm backstop |
| Overdraft | **Cho phép credit âm** (không clamp) — reseller có `user.credit < 0` là cờ để rà soát/đối soát trên DB |
| Release | Ngầm: cancel/reject tự rớt reserve. decline_payment giữ cho retry |
| Ledger debit | **Ghi** `CreditTransaction(direction=debit, amount dương, transId=null, type=use_credit_for_order)` trong cùng transaction với claim + trừ credit |
| Field `type` | `credit_transaction.type` (string const, NOT NULL): `add_credit_from_order` / `add_credit_by_admin` / `use_credit_for_order`. `setType()` tự đồng bộ `direction` |

## 3. Thay đổi code (commit `c38a8556`)

| File | Nội dung |
|---|---|
| `app/src/Entity/ReferralOrder/ReferralOrder.php` | field `creditUse` + `creditDeductedAt` + getter/setter + `getGrossTotalAfterTax()`; hằng `CREDIT_RESERVE_STATUSES` |
| `app/migrations/Version20260630104644.php` | `ALTER TABLE referral_order ADD credit_use` (đã chạy dev) |
| `app/migrations/Version20260701023808.php` | `ALTER TABLE referral_order ADD credit_deducted_at` (khóa idempotency, đã chạy dev) |
| `app/src/Repository/ReferralOrder/ReferralOrderRepository.php` | `sumReservedCreditUse(User, ?excludeOrderId)` |
| `app/src/Service/Credit/CreditService.php` (MỚI) | `availableCredit()`, `deductForPaidOrder()` — claim atomic `credit_deducted_at` + persist `CreditTransaction(debit, type=use_credit_for_order)` + trừ `user.credit` (allow âm), tất cả trong 1 transaction |
| `app/src/Entity/Credit/CreditTransaction.php` | thêm const `TYPE_*` (3 giá trị) + field `type` + `setType()` (tự set `direction` qua map) |
| `app/migrations/Version20260701073630.php` | `ALTER TABLE credit_transaction ADD type` + backfill theo `direction` + SET NOT NULL (đã chạy dev) |
| `app/src/MessageHandler/Credit/CreditResellerBalanceMessageHandler.php` (inflow) | đổi `setDirection(CREDIT)` → `setType(TYPE_ADD_CREDIT_FROM_ORDER)` |
| `app/src/Service/ReferralOrder/ReferralOrderService.php` | `applyCreditUse()` (validate PTI/≤gross/≤available, trừ totalAfterTax) gọi trong `applyCrmProductData`; param `creditUse` ở `create()`/`update()` |
| `Mutation/Create/{Input,Resolver}.php` | field `credit_use`; truyền `creditUse`; nhánh ==0 set PAID + refresh + dispatch `AddAgentProductStock`/`OrderPaid`/`DeductOrderCredit` thủ công |
| `Mutation/Update/{Input,Resolver}.php` | field `credit_use`; truyền `creditUse`; nhánh ==0 set PAID (UPDATE → trigger → subscriber) |
| `Mutation/PreviewOrder/{Input,Resolver}.php` | field `credit_use`; truyền `creditUse` (preview trả totalAfterTax đã trừ) |
| `ReferralOrder/ReferralOrderEntityType.php` | expose `credit_use` (SourceField) |
| `app/src/Message/Credit/DeductOrderCreditMessage.php` (MỚI) | message (orderId) |
| `app/src/MessageHandler/Credit/DeductOrderCreditMessageHandler.php` (MỚI) | re-check PAID+PTI (chống race void), gọi `CreditService::deductForPaidOrder` |
| `app/src/EventSubscriber/Hasura/ReferralOrderPaidSubscriber.php` | nhánh PTI: dispatch `DeductOrderCreditMessage` khi `credit_use>0` |
| `app/hasura/.../public_referral_order.yaml` | cột `credit_use` vào select-perms (ROLE_USER + ROLE_HASURA_CRM) |
| `docs/async-messages.md` | regenerate |

## 4. Thay đổi Hasura remote-schema (CHƯA commit — uncommitted)

Hand-add `credit_use` vào remote-schema permissions (enforce bật):
- `remote_schemas/local/permissions/role_roleuser.yaml` (×4: output `referral_order_entity_type` + input create/update/preview)
- `role_rolehasuracrm.yaml` (×1: output)
- `role_roleanonymous.yaml` (×1: output)

> Workflow Hasura đã chốt: **reload → apply → persist → export**. KHÔNG commit output `hasura:metadata:export` (tạo noise: thêm description + reorder type/field) → hand-add `credit_use` vào yml thay thế. Live dev Hasura đã được `persist` cập nhật đúng.

## 5. Luồng runtime

```
CREATE/UPDATE/PREVIEW (PTI, credit_use>0)
  validate: PTI + credit_use ≤ gross + credit_use ≤ available
  totalAfterTax = gross − credit_use ; reserve ngầm (đơn vào reserve-status)

PAY NOW, totalAfterTax == 0  (credit phủ hết)
  Create: set PAID (INSERT) → refresh → dispatch AddAgentProductStock + OrderPaid + DeductOrderCredit
  Update: set PAID (UPDATE) → Hasura trigger ReferralOrderPaid → subscriber lo

PAY NOW, totalAfterTax > 0
  FE redirect gateway charge phần còn lại → CRM paid → TransactionUpdate → status PAID
  → ReferralOrderPaidSubscriber (PTI) → DeductOrderCreditMessage
  → handler → CreditService: claim credit_deducted_at (atomic) + −User.credit atomic (idempotent)

CANCELLED / CLIENT_REJECTED → rớt reserve → credit thả (không code)
DECLINE_PAYMENT → vẫn reserve (retry)
```

## 6. Verify đã chạy (✅)

- `php bin/console cache:clear` — DI container compile OK (CreditService, MessageBus inject OK).
- `graphqlite:dump-schema` — `credit_use: Float` xuất hiện 4 chỗ (3 input + 1 output).
- `doctrine:schema:validate` — mapping + DB in sync.
- `php -l` — 10 file đổi/mới: PASS.
- `debug:messenger` — `DeductOrderCreditMessage → DeductOrderCreditMessageHandler` registered.
- `hasura:metadata:apply` — yml committed (đã hand-add credit_use) hợp lệ, consistent với Hasura.
- `composer dump-autoload` — cần cho class mới (gotcha đã biết).

## 7. CHƯA làm / cần làm tiếp

- **Test end-to-end runtime** (chưa chạy): cần seed credit (tạo DepositOrder `resell_type=deposit` thật, hoặc `UPDATE "user" SET credit=...`) rồi kiểm:
  1. preview/create/update đơn PTI có `credit_use` → `totalAfterTax` giảm đúng; validate reject khi vượt available/gross/không phải PTI.
  2. pay-now `==0` → order PAID + AddAgentProductStock + `User.credit` giảm + `CreditTransaction(DEBIT)` tạo.
  3. pay-now `>0` → charge phần còn lại → CRM paid → DEBIT + `User.credit` giảm; redeliver không trừ đôi.
  4. cancel → credit "thả" (available tăng lại); decline → giữ → retry → trừ đúng.
  5. overdraft (2 đơn song song) → guard clamp về 0 + log.
- **Commit nốt** 3 file remote-schema yml vào commit feature (đang uncommitted). `.claude/settings.json` là harness tự sửa, không thuộc feature.
- **Out of scope** (ADR-0005, phase sau): refund/reverse credit khi đơn đã paid bị void/cancel (phantom balance); top-up (đã có).

## 8. Rủi ro / lưu ý

- **Khe async paid→debit** (Q2): available tính theo status nên có khe rất ngắn giữa `paid` và lúc handler trừ; chấp nhận (credit phase 1 chỉ tăng + mọi debit đều reserve).
- **Reserve race khi validate** (2 create song song): có thể over-reserve hiếm; phase 1 chấp nhận.
- **`AddAgentProductStockMessageHandler` idempotency**: nhánh ==0 create dispatch thủ công, nhánh khác qua trigger — không double vì create=INSERT (no trigger), update/>0=trigger only. Vẫn nên xác nhận handler idempotent theo order khi test.
- **Tên `deposit`**: `RESELL_TYPE_DEPOSIT` = nạp (inflow, đã có) ≠ credit dùng cho `purchase_to_inventory` (debit, task này). Đừng nhầm.
