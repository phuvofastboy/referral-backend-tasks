# D8-426 / 07 — Implementation Plan: Refund credit khi cancel đơn đã paid

> Yêu cầu & Q&A đã chốt: [`01-description.md`](01-description.md). Nối tiếp DEBIT phase [`../03-plan.md`](../03-plan.md) / [`../04-result.md`](../04-result.md).
> Ngày: 2026-07-02.

## 0. Mục tiêu (1 câu)
Đơn `purchase_to_inventory` **đã paid + đã thực trừ credit** (`credit_deducted_at != null`), khi bị **CRM cancel** (`paid → cancelled`) → **cộng lại `credit_use` vào `User.credit`** + ghi `CreditTransaction(type=add_credit_from_order_refund)`, **idempotent** qua cột mới `credit_refunded_at`.

## 1. Quyết định đã chốt (nhắc lại từ Q&A)
| # | Chốt |
|---|------|
| Constraint | `CreditTransaction→ReferralOrder`: **OneToOne UNIQUE → ManyToOne** (1 order nhiều ledger row) |
| Trigger status | **Chỉ `paid → cancelled`** |
| Idempotency | Cột **`referral_order.credit_refunded_at`**, claim atomic |
| Số tiền | **Full `credit_use`**, chỉ khi `credit_deducted_at IS NOT NULL` |

---

## 2. Thay đổi data model

### 2.1 `CreditTransaction` — quan hệ + type mới
File: `app/src/Entity/Credit/CreditTransaction.php`
- Thêm hằng:
  ```php
  public const TYPE_ADD_CREDIT_FROM_ORDER_REFUND = 'add_credit_from_order_refund';
  ```
- Thêm vào map `DIRECTION_BY_TYPE`: `TYPE_ADD_CREDIT_FROM_ORDER_REFUND => DIRECTION_CREDIT`.
- **Đổi owning side** (line 74-76):
  ```php
  // TỪ:
  #[ORM\JoinColumn(nullable: false)]
  #[ORM\OneToOne(inversedBy: 'creditTransaction', targetEntity: ReferralOrder::class)]
  private ?ReferralOrder $referralOrder = null;
  // THÀNH:
  #[ORM\JoinColumn(nullable: false)]
  #[ORM\ManyToOne(inversedBy: 'creditTransactions', targetEntity: ReferralOrder::class)]
  private ?ReferralOrder $referralOrder = null;
  ```
- Lưu ý: `type` column `length: 32` — `'add_credit_from_order_refund'` = 28 ký tự → **vẫn vừa** (không cần nới cột).

### 2.2 `ReferralOrder` — inverse side + cột idempotency
File: `app/src/Entity/ReferralOrder/ReferralOrder.php`
- **Đổi inverse side** (line 288-289):
  ```php
  // TỪ:
  #[ORM\OneToOne(mappedBy: 'referralOrder', targetEntity: CreditTransaction::class)]
  private ?CreditTransaction $creditTransaction = null;
  // THÀNH:
  #[ORM\OneToMany(mappedBy: 'referralOrder', targetEntity: CreditTransaction::class)]
  private Collection $creditTransactions;
  ```
  - Init `$this->creditTransactions = new ArrayCollection();` trong constructor.
  - `getCreditTransaction()`/`setCreditTransaction()` (line 724-732): **không dùng functionally ở đâu** (đã grep toàn repo — chỉ định nghĩa). → **Xóa** 2 method này + thay bằng `getCreditTransactions(): Collection`. (Nếu muốn an toàn tối đa: giữ `getCreditTransaction()` trả `$this->creditTransactions->first() ?: null` — nhưng ưu tiên xóa cho sạch.)
- Thêm cột idempotency (đối xứng `creditDeductedAt` line 268):
  ```php
  #[ORM\Column(type: 'datetime', nullable: true)]
  private ?\DateTimeInterface $creditRefundedAt = null;
  // + getter/setter
  ```

### 2.3 Migration
`php bin/console doctrine:migrations:diff` → kỳ vọng migration chứa:
- `ALTER TABLE referral_order ADD credit_refunded_at TIMESTAMP(0) ... DEFAULT NULL`
- **DROP UNIQUE index** trên `credit_transaction.referral_order_id` + tạo lại index thường (do OneToOne→ManyToOne).
- Review bằng skill **db-migration-safety** (drop unique = safe, add nullable column = safe; kiểm Hasura metadata `credit_transaction`/`referral_order` không phụ thuộc unique cũ).
- Hasura: expose `referral_order.credit_refunded_at` **KHÔNG bắt buộc** (nội bộ, không cần cho FE) — bỏ qua trừ khi muốn debug qua console.

---

## 3. Service layer — `CreditService::refundForCancelledOrder()`
File: `app/src/Service/Credit/CreditService.php` (thêm method, **đối xứng** `deductForPaidOrder`)
```php
/**
 * Refund credit_use back to User.credit for a paid PTI order cancelled from CRM, and record a
 * `add_credit_from_order_refund` CreditTransaction. Idempotent: atomically claims via
 * referral_order.credit_refunded_at, so at-least-once redelivery refunds once. Only orders that were
 * actually debited (credit_deducted_at IS NOT NULL) are refunded.
 */
public function refundForCancelledOrder(ReferralOrder $order): void
{
    $amount = round((float) $order->getCreditUse(), 2);
    if ($amount <= 0.0) {
        return;
    }

    $user = $order->getCreatedBy();
    if (!$user instanceof User) {
        $this->logger->warning('Credit refund skip: order has no creator', [...]);
        return;
    }

    $orderId = $order->getId()?->__toString();
    $uid     = $user->getId()?->__toString();

    $this->entityManager->wrapInTransaction(function () use ($order, $user, $orderId, $uid, $amount): void {
        $connection = $this->entityManager->getConnection();

        // Atomic claim: chỉ đơn ĐÃ trừ credit và CHƯA refund mới được hoàn (idempotency guard).
        $claimed = $connection->executeStatement(
            'UPDATE referral_order SET credit_refunded_at = now() '
            . 'WHERE id = CAST(:id AS uuid) AND credit_use > 0 '
            . 'AND credit_deducted_at IS NOT NULL AND credit_refunded_at IS NULL',
            ['id' => $orderId],
        );
        if ($claimed === 0) {
            return;
        }

        $transaction = new CreditTransaction();
        $transaction->setUser($user);
        $transaction->setReferralOrder($order);
        $transaction->setAmount($amount);
        $transaction->setType(CreditTransaction::TYPE_ADD_CREDIT_FROM_ORDER_REFUND); // tự set direction=credit
        $transaction->setStatus(CreditTransaction::STATUS_COMPLETED);
        $this->entityManager->persist($transaction);
        $this->entityManager->flush();

        // Atomic increment (đối xứng debit; +amount).
        $connection->executeStatement(
            'UPDATE "user" SET credit = round(CAST(COALESCE(credit, 0) + :amount AS numeric), 2) '
            . 'WHERE id = CAST(:id AS uuid)',
            ['amount' => $amount, 'id' => $uid],
        );
    });

    $this->logger->info('Refunded reseller credit', ['order_id' => $orderId, 'uid' => $uid, 'amount' => $amount]);
}
```
> Guard `credit_deducted_at IS NOT NULL` trong SQL claim = đảm bảo **không hoàn cho đơn chưa từng trừ** (vd đơn cancel khi chưa paid — dù nhánh đó không tới đây, vẫn phòng thủ). Đối xứng hoàn hảo với `deductForPaidOrder`.

---

## 4. Message + Handler (đối xứng `DeductOrderCredit*`)
### 4.1 `app/src/Message/Credit/RefundOrderCreditMessage.php` (MỚI)
Copy y `DeductOrderCreditMessage` (implements `AsyncMailMessageInterface`, field `referralOrderId` + getter → auto route `async_common`).

### 4.2 `app/src/MessageHandler/Credit/RefundOrderCreditMessageHandler.php` (MỚI)
```php
public function __invoke(RefundOrderCreditMessage $message): void
{
    $order = $this->referralOrderRepository->find($message->getReferralOrderId());
    if ($order === null) { $this->logger->error('Credit refund skip: order not found', [...]); return; }

    // Re-check chống race: chỉ hoàn khi PTI + đang cancelled + đã từng trừ credit.
    if (!$order->isPurchaseToInventory()
        || $order->getStatus() !== ReferralOrder::STATUS_CANCELLED
        || $order->getCreditDeductedAt() === null) {
        return;
    }

    $this->creditService->refundForCancelledOrder($order);
}
```
> Idempotency thật nằm ở claim `credit_refunded_at` trong service; re-check ở đây chỉ để skip sớm.

---

## 5. Điểm móc nối — dispatch khi `paid → cancelled`
File: `app/src/EventSubscriber/Hasura/ReferralOrderMutationSubscriber.php` (đối xứng `ReferralOrderPaidSubscriber` dispatch `DeductOrderCreditMessage`).

Trong block `INSERT|UPDATE`, đã có `$referralOrderOldStatus` / `$referralOrderNewStatus`. Thêm:
```php
use App\Entity\ReferralOrder\ReferralOrder;
use App\Message\Credit\RefundOrderCreditMessage;
...
if ($referralOrderOldStatus === ReferralOrder::STATUS_PAID
    && $referralOrderNewStatus === ReferralOrder::STATUS_CANCELLED) {
    $this->messageBus->dispatch(new RefundOrderCreditMessage($referralOrderId));
}
```
> **Vì sao chọn subscriber, KHÔNG cắm vào `ReferralOrderUpdateStatusMessageHandler`:** handler đó gọi `updateCrmProductStock` (INCREASE) **không idempotent** — nếu gộp refund vào cùng message rồi handler retry sẽ cộng kho CRM 2 lần. Tách `RefundOrderCreditMessage` độc lập → refund retry riêng (idempotent qua claim), không đụng luồng restore stock.
> Subscriber chỉ cần `old`/`new` status (chắc chắn có trong payload trigger `ReferralOrderMutation` — đã dùng ở line 42-43). Handler tự re-load entity → không cần `credit_use` trong payload.

---

## 6. Cập nhật chỗ dùng quan hệ cũ (do OneToOne→ManyToOne)
- Grep xác nhận `getCreditTransaction()` **không** dùng functionally (đã kiểm: chỉ định nghĩa + 1 comment ở `TransactionUpdateHandler:98`). → Xóa an toàn.
- `CreditSummaryService` dùng `CreditTransactionRepository::sumPendingCreditForUser` (không đụng quan hệ trên ReferralOrder) → **không ảnh hưởng**.
- `doctrine:schema:validate` sau khi đổi để chắc mapping ⇄ DB khớp.

---

## 7. Idempotency & concurrency (tổng hợp)
- **Double-refund**: claim `credit_refunded_at IS NULL` (atomic UPDATE, `affected=1` mới hoàn) chặn redeliver / trigger fire lặp.
- **Refund đơn chưa từng trừ**: claim yêu cầu `credit_deducted_at IS NOT NULL` → không hoàn.
- **Race cancel↔debit async** (đơn vừa paid, debit chưa chạy thì cancel tới): nếu `credit_deducted_at` còn NULL lúc refund claim → refund skip (đúng: chưa trừ thì không hoàn). Debit sau đó sẽ trừ. **Rủi ro tồn dư:** đơn cancelled nhưng debit chạy sau vẫn trừ (vì `deductForPaidOrder` claim theo `credit_deducted_at IS NULL`, không check status). → **Xem §8 R1.**
- **+User.credit atomic** (no read-modify-write) → không lost update khi refund/debit song song.

---

## 8. Rủi ro / cần verify khi code
### R1 — Race: cancel trước khi debit async kịp chạy
Luồng >0: paid → `DeductOrderCreditMessage` enqueue. Nếu CRM cancel ngay → `RefundOrderCreditMessage` enqueue. Hai message có thể chạy bất kỳ thứ tự.
- Refund trước (deducted_at NULL) → skip. Debit sau → trừ. **Kết quả sai: credit bị trừ mà đơn đã cancel.**
- **Giảm thiểu:** `DeductOrderCreditMessageHandler` đã re-check `getStatus() === STATUS_PAID` (line 34) → nếu đã cancelled thì debit **skip**. ✅ Đã có phòng thủ. Cần **verify** lại: order status lúc handler debit chạy phản ánh cancelled → debit bị chặn. → An toàn.
- Case ngược (debit xong rồi cancel): refund thấy `deducted_at != null` → hoàn đúng. ✅

### R2 — Đơn `==0` (credit phủ hết, không qua gateway) bị cancel
Đơn create-as-paid (E1) cũng có `credit_deducted_at != null` → cancel → refund cộng lại đúng `credit_use`. ✅ Không cần xử lý riêng.

### R3 — `client_rejected` sau paid
**Out of scope** (Q2 chốt chỉ `cancelled`). Nếu sau này cần: thêm nhánh tương tự.

### R4 — Hasura metadata phụ thuộc unique cũ
Drop unique `credit_transaction.referral_order_id` có thể ảnh hưởng object-relationship trong Hasura metadata (nếu khai `referral_order` là object relationship dựa unique). → **Verify** `app/hasura/.../credit_transaction*.yaml`; đổi sang array-relationship phía referral_order nếu cần. Chạy `hasura:metadata:apply` để bắt lỗi consistency.

### R5 — `type` length
`add_credit_from_order_refund` = 28 ≤ 32. OK, không cần migration nới cột.

---

## 9. Checklist file đụng tới
- [ ] `app/src/Entity/Credit/CreditTransaction.php` — const `TYPE_ADD_CREDIT_FROM_ORDER_REFUND` + map direction + OneToOne→ManyToOne
- [ ] `app/src/Entity/ReferralOrder/ReferralOrder.php` — inverse OneToMany `creditTransactions` + field `creditRefundedAt` + getter/setter; xóa `getCreditTransaction/setCreditTransaction`
- [ ] `app/migrations/Version*.php` — `ADD credit_refunded_at` + DROP unique index `credit_transaction.referral_order_id`
- [ ] `app/src/Service/Credit/CreditService.php` — `refundForCancelledOrder()`
- [ ] `app/src/Message/Credit/RefundOrderCreditMessage.php` — MỚI
- [ ] `app/src/MessageHandler/Credit/RefundOrderCreditMessageHandler.php` — MỚI
- [ ] `app/src/EventSubscriber/Hasura/ReferralOrderMutationSubscriber.php` — dispatch `RefundOrderCreditMessage` khi paid→cancelled
- [ ] `app/hasura/.../credit_transaction*.yaml` — verify relationship sau drop unique (R4)
- [ ] Regenerate: `python3 scripts/extract_async_messages.py > docs/async-messages.md`

> KHÔNG tạo: cột/field credit debit (đã có). KHÔNG đụng luồng release ngầm (đơn chưa paid). KHÔNG làm partial cancel / client_rejected.

## 10. Thứ tự thực hiện
1. `CreditTransaction`: const type mới + map + đổi ManyToOne.
2. `ReferralOrder`: inverse OneToMany + `creditRefundedAt` + bỏ getter/setter cũ.
3. `doctrine:migrations:diff` → review (skill db-migration-safety) → migrate dev.
4. `CreditService::refundForCancelledOrder()`.
5. `RefundOrderCreditMessage` + Handler.
6. Dispatch trong `ReferralOrderMutationSubscriber`.
7. Verify Hasura metadata (R4) + `hasura:metadata:apply`.
8. Static checks: `composer dump-autoload`, `cache:clear`, `schema:validate`, `debug:messenger | grep RefundOrderCredit`, `php -l`.
9. Regenerate `docs/async-messages.md`.
10. Test end-to-end (§11).

## 11. Test plan (DB-driven, chưa có test runner)
Seed: đơn PTI đã paid + `credit_use=30` + `credit_deducted_at` set + `user.credit` đã bị trừ (vd 470).
1. **Happy path:** `UPDATE referral_order SET status='cancelled' WHERE id=...` (old=paid) → trigger → subscriber → `RefundOrderCreditMessage` → consume → `user.credit = 500` (470+30), `credit_refunded_at` set, có row `CreditTransaction(type=add_credit_from_order_refund, amount=30)`.
2. **Idempotency:** re-fire event / re-consume → `user.credit` VẪN 500, không tạo row refund thứ 2 (claim fail).
3. **Đơn chưa từng trừ:** đơn `credit_deducted_at IS NULL` bị cancel → refund skip (claim=0), credit không đổi.
4. **==0 order (E1) cancel:** `credit_use=gross`, paid → cancel → hoàn full `credit_use`.
5. **Race R1:** paid → cancel gần như đồng thời → xác nhận debit skip khi status=cancelled (DeductOrderCreditMessageHandler re-check) → credit về đúng.
6. **Cột available:** sau refund, đơn cancelled không nằm reserve-set → `availableCredit` phản ánh credit đã cộng lại đúng.

## 12. Out of scope
- Partial cancel / partial refund.
- Refund khi `client_rejected` / void bằng path khác sau paid.
- Refund cho resell_type khác PTI.
- UI/summary hiển thị lịch sử refund (nếu cần → task riêng).
