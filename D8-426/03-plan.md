# D8-426 — Implementation Plan: Thanh toán order bằng Deposit Balance (credit) — DEBIT phase

> Yêu cầu & quyết định: xem [`02-task-description.md`](02-task-description.md). Tham chiếu: [`docs/domains/credit.md`](../../docs/domains/credit.md), [ADR-0005](../../docs/adr/0005-reseller-credit-balance-via-topup-order.md).
> Cập nhật: 2026-06-30 (sau khi rebase infra credit từ develop + chốt mô hình reserve-via-status).

> ⚠️ **REV 4 (2026-07-01) — idempotency debit ĐỔI:** KHÔNG dùng `CreditTransaction`/`getCreditTransaction()` làm khóa (bảng đó thuần inflow/top-up). Thay bằng cột **`referral_order.credit_deducted_at`** (timestamp) — claim atomic `UPDATE referral_order SET credit_deducted_at=now() WHERE id=:id AND credit_use>0 AND credit_deducted_at IS NULL` (affected=1 mới trừ). Luồng debit KHÔNG ghi row `credit_transaction`. Các đoạn bên dưới nhắc `getCreditTransaction()`/`CreditTransaction(DEBIT)` đã bị thay — bản as-built xem [`04-result.md`](04-result.md).

## 0. Bối cảnh: hạ tầng credit ĐÃ CÓ (inflow), task này là DEBIT phase

Team khác đã build **phía nạp credit (INFLOW)** và ADR-0005 ghi rõ **DEBIT/spend là "phase sau" = task D8-426 này**. Đã có sẵn:
- `User.credit` (scalar float, **phase 1 chỉ tăng**).
- `CreditTransaction` ledger: `OneToOne → ReferralOrder` (UNIQUE = idempotency key), `amount`, `user`, **`direction` (CREDIT/DEBIT)**, `transId`, `createdAt/updatedAt`. **KHÔNG có `status`** (cố ý — xem ADR-0005).
- Nạp credit qua **DepositOrder** (`resell_type = RESELL_TYPE_DEPOSIT`) → paid → `CreditResellerBalanceMessage` → `+credit` **atomic SQL UPDATE**, guard `getCreditTransaction() !== null`. (`CreditResellerBalanceMessageHandler` — dùng làm khuôn cho handler debit của ta.)

> ⚠️ Lưu ý va chạm tên: `RESELL_TYPE_DEPOSIT` = **nạp** credit (inflow, đã có). Task này dùng credit để trả cho đơn **`purchase_to_inventory` (PTI)** = **tiêu** credit (debit). Hai thứ khác nhau, đừng nhầm.

## 1. Mô hình DEBIT đã chốt (reserve-via-status)

**KHÔNG** hold qua `credit_transaction` (không có status). **Hold ngầm qua `referral_order.status`:**

- **Lưu ý định**: thêm cột `referral_order.credit_use` (chưa có) — số credit muốn dùng cho đơn.
- **Reserve = đơn có `credit_use > 0` đang ở reserve-status.** Reserve-status (Q1): `draft, sent, viewed, signed, pending_payment, decline_payment`. KHÔNG reserve: `paid, cancelled, client_rejected`.
- **available credit (Q2)**: `available = user.credit − SUM(credit_use của các đơn của user ở reserve-status)`. Validate `credit_use` dựa vào số này (chấp nhận khe rất ngắn giữa paid và lúc trừ async).
- **Reserve áp mọi đơn có credit_use>0 (Q3)** — bất kể pay-now.
- **Deduct tại paid**: trừ `credit_use` vào `user.credit` **atomic** + tạo `CreditTransaction(direction=DEBIT)` (bút toán + idempotency key). Mirror đúng `CreditResellerBalanceMessageHandler`.
- **Release = ngầm**: đơn chuyển `cancelled`/`client_rejected` → tự rớt khỏi reserve-status → SUM không tính → credit "thả" ra, **không cần thao tác**. `decline_payment` **vẫn reserve** (giữ cho retry).
- **Overdraft guard tại paid (Q4)**: `UPDATE ... SET credit = round(credit - :amount,2) WHERE credit >= :amount`; nếu 0 row affected (không đủ) → trừ phần còn lại (clamp về 0) + log warning; DEBIT row ghi theo số thực trừ. **Credit không bao giờ âm.**

---

## 2. Thay đổi data model (đợt này)

### 2.1 `ReferralOrder.creditUse` (MỚI — cần thêm)
File: `app/src/Entity/ReferralOrder/ReferralOrder.php`
- Thêm `private ?float $creditUse = null;` + getter/setter. (Cột `credit_use` chưa có trong migration team — ta tự thêm.)
- Migration `diff` → chỉ 1 cột nullable → an toàn. Review bằng skill **db-migration-safety**.
- Hasura: expose `referral_order.credit_use` (select + input nếu FE ghi qua Hasura).

> `User.credit` + `credit_transaction` + `CreditTransaction` entity/repo: **ĐÃ CÓ**, không tạo lại.

---

## 3. Input thay đổi
Thêm field `credit_use` (Float, `#[Assert\PositiveOrZero]`) vào:
- `app/src/GraphQL/ReferralOrder/Mutation/Create/Input.php`
- `app/src/GraphQL/ReferralOrder/Mutation/Update/Input.php`
- `app/src/GraphQL/ReferralOrder/Mutation/PreviewOrder/Input.php` (D2 — preview trả total đã trừ)

Validate "PTI + ≤gross + ≤available" làm ở **service** (§4.2). Lưu ý `isQuickSubmit` (Update:305) loại trừ `credit_use` khỏi đếm field để không phá điều kiện quick-submit.

---

## 4. Service layer

### 4.1 Truyền `credit_use` + set lên order
File: `ReferralOrderService::create()` / `update()`:
- Thêm param `?float $creditUse = null`.
- `$order->setCreditUse($creditUse);` **trước** `applyCrmProductData()`.
- Resolver (Create/Update/Preview) truyền `creditUse: $inputObj->creditUse`.

### 4.2 Validate + trừ totalAfterTax (tại `applyCrmProductData`, ~line 579)
```php
$grossAfterTax = round($orderTotalAfterTax, 2);
$creditUse     = $order->getCreditUse() ?? 0.0;

if ($creditUse > 0) {
    if (!$order->isPurchaseToInventory()) {                 // chỉ PTI
        throw new GraphQLException('Credit can only be used for purchase_to_inventory orders');
    }
    if ($creditUse > $grossAfterTax) {                       // ≤ tổng đơn
        throw new GraphQLException('credit_use exceeds order total');
    }
    $creator   = $order->getCreatedBy() ?? $this->security->getUser();
    $available = $this->creditService->availableCredit($creator, $order); // credit − SUM(reserve), loại trừ chính order khi update
    if ($creditUse > $available) {                           // ≤ available
        throw new GraphQLException('Insufficient credit balance');
    }
}

$order->setTotalAfterTax(round($grossAfterTax - $creditUse, 2)); // charge gateway = phần còn lại
```
- `applyCrmProductData` chạy cả ở Preview → preview tự trả total đã trừ.
- Inject `CreditService` (mới) hoặc `CreditTransactionRepository` + query SUM.

### 4.3 `CreditService` (MỚI) — `app/src/Service/Credit/CreditService.php`
- `availableCredit(User $u, ?ReferralOrder $exclude = null): float` — `u.credit − SUM(o.creditUse) WHERE o.createdBy=u AND o.creditUse>0 AND o.status IN (reserveSet) AND o.id != exclude`. (Query trong `ReferralOrderRepository` hoặc `CreditTransactionRepository`.)
- `deductForPaidOrder(ReferralOrder $order, ?string $transId): void` — dùng cho cả nhánh ==0 (resolver) và >0 (handler):
  ```php
  if ($order->getCreditTransaction() !== null) return;   // idempotent (OneToOne UNIQUE)
  $amount = round((float)$order->getCreditUse(), 2);
  if ($amount <= 0) return;
  $user = $order->getCreatedBy();
  $this->em->wrapInTransaction(function () use ($order,$user,$amount,$transId) {
      $tx = (new CreditTransaction())
          ->setUser($user)->setReferralOrder($order)
          ->setAmount($amount)->setDirection(CreditTransaction::DIRECTION_DEBIT)
          ->setTransId($transId);
      $this->em->persist($tx); $this->em->flush();        // UNIQUE = backstop idempotency
      // atomic, guard, clamp về 0, log nếu thiếu (Q4)
      $affected = $conn->executeStatement(
        'UPDATE "user" SET credit = round(CAST(credit - :amt AS numeric),2) WHERE id = CAST(:id AS uuid) AND credit >= :amt',
        ['amt'=>$amount,'id'=>$uid]);
      if ($affected === 0) {
        $conn->executeStatement('UPDATE "user" SET credit = 0 WHERE id = CAST(:id AS uuid)', ['id'=>$uid]);
        $logger->warning('Credit debit clamped (insufficient)', [...]);
      }
  });
  ```

> ⚠️ atomicity: handler debit cũng dispatch qua message → đảm bảo consume dưới bus có middleware transactional, hoặc `wrapInTransaction` tường minh (giống inflow). ADR-0005 risk note đã nêu.

---

## 5. Resolver — nhánh pay-now / mark-paid khi credit phủ hết (hướng b)

Trong Create & Update resolver, **sau** khi gọi `orderService->create()/update()` (totalAfterTax đã = gross − credit_use):

```php
$creditUse = $order->getCreditUse() ?? 0.0;
if ($isSubmitOrder && $isPayNow && $order->isPurchaseToInventory() && $creditUse > 0
    && $order->getTotalAfterTax() <= 0.00) {
    // credit phủ hết → mark paid (không charge gateway)
    $order->setStatus(ReferralOrder::STATUS_PAID);                 // bypass transition table có chủ đích (§5.1)
    $this->creditService->deductForPaidOrder($order, null);        // transId=null (Q5: ==0 không có gateway trans)
    // downstream: xem §5.1 (dispatch thủ công nếu là INSERT/create)
}
// remaining > 0: KHÔNG làm gì thêm ở resolver → FE redirect gateway charge phần totalAfterTax còn lại;
//                deduct credit xảy ra ở §6 khi CRM báo paid.
```

### 5.1 Mark-paid + downstream khi ==0 (D1)
- Set `STATUS_PAID` **không** qua `isValidStatusTransition()` (giống MarkPaid/ADR-0002). Guard: owner + PTI + pay-now + `totalAfterTax<=0`. KHÔNG thêm edge vào `VALID_STATUS_TRANSITIONS`.
- ✅ Verified: trigger `ReferralOrderPaid` chỉ fire **UPDATE** (`public_referral_order.yaml:241`), không INSERT.
  - **Create + ==0** (INSERT thẳng paid): trigger im → resolver **dispatch thủ công** `AddAgentProductStockMessage` + `OrderPaidMessage` (`DispatchAfterCurrentBusStamp`), và đã `deductForPaidOrder()` ở trên.
  - **Update sent→paid + ==0** (UPDATE): trigger fire → `ReferralOrderPaidSubscriber` lo stock + (qua §6) deduct. Để tránh double: `deductForPaidOrder` idempotent qua `getCreditTransaction() !== null`; kiểm `AddAgentProductStockMessageHandler` idempotent theo order. → Có thể để resolver chỉ `setStatus(PAID)` cho nhánh update và để subscriber lo hết; nhưng vì `deductForPaidOrder` đã idempotent, gọi ở resolver vẫn an toàn.

---

## 6. Deduct credit khi CRM báo paid (nhánh remaining > 0)
File: `app/src/EventSubscriber/Hasura/ReferralOrderPaidSubscriber.php`, nhánh PTI (line 112):
```php
if ($resellType === ReferralOrder::RESELL_TYPE_PURCHASE_TO_INVENTORY) {
    $this->bus->dispatch(new AddAgentProductStockMessage($newData['id']));
    $this->bus->dispatch(new DeductOrderCreditMessage($newData['id'], $newData['...transId?']));  // MỚI
    return;
}
```
- `app/src/Message/Credit/DeductOrderCreditMessage.php` (orderId [+transId]) — `AsyncMailMessageInterface` → `async_common`, hoặc bus có transactional.
- `app/src/MessageHandler/Credit/DeductOrderCreditMessageHandler.php`:
  - load order; re-check `isPurchaseToInventory() && status===PAID` (giống inflow re-check void-race);
  - `$this->creditService->deductForPaidOrder($order, $transId);` (đã idempotent + atomic + clamp).

> transId nhánh >0: lấy từ payload paid event nếu có; không thì null (DEBIT row vẫn hợp lệ).

---

## 7. Release credit — KHÔNG cần code riêng
Đơn `cancelled`/`client_rejected` tự rớt khỏi reserve-status → `availableCredit` không tính nữa → credit "thả" tự động. `decline_payment` vẫn reserve (D3, cho retry). → **Không** đụng khối cancel resolver / `TransactionUpdateHandler` cho việc release.

---

## 8. Idempotency & concurrency (tổng hợp)
- **Double-deduct**: `getCreditTransaction() !== null` (OneToOne UNIQUE) chặn — dù subscriber fire nhiều lần, hay cả resolver(==0) lẫn subscriber(update) cùng chạy.
- **Overdraft**: atomic guard `WHERE credit >= amount` + clamp về 0 + log (Q4) → credit không âm.
- **Reserve double-spend khi validate**: 2 create song song cùng đọc available rồi cùng tạo đơn có thể over-reserve (khe hiếm). Phase 1 chấp nhận (credit chỉ tăng + debit đều reserve); có thể siết bằng lock/transaction sau nếu cần.
- **Khe paid→debit async (Q2)**: chấp nhận (rất ngắn).

---

## 9. Trả về FE
`ReferralOrderEntityType.php`: thêm `SourceField` `credit_use`; `total_after_tax` (đã trừ) expose sẵn. FE đọc `totalAfterTax`==0 → không redirect; >0 → charge phần còn lại.

---

## 10. Edge cases / verify khi code
1. ✅ D1: trigger chỉ UPDATE → ==0 create dispatch thủ công (§5.1). Kiểm `AddAgentProductStockMessageHandler` idempotent.
2. ✅ D2: PreviewOrder nhận credit_use.
3. **Insider agent** ép `isPayNow=true` (Create:127/Update:204) — verify luồng credit hợp lệ.
4. **isQuickSubmit** (Update:305) — loại `credit_use` khỏi đếm field.
5. **PTI không tạo commission** (`TransactionUpdateHandler:80-82` skip khi `isPurchaseToInventory()` / `isDeposit()`) — credit debit không đụng commission. OK.
6. **Vet switch `getEffectiveResellType()`** — đảm bảo credit_use chỉ tác động PTI, không rò sang `deposit`/`sell_via_crm`/`sell_from_inventory`.
7. **transId nhánh >0**: xác nhận field nào trong paid-event payload mang được transId (nếu cần).

---

## 11. Checklist file đụng tới
- [ ] `app/src/Entity/ReferralOrder/ReferralOrder.php` — field `creditUse`
- [ ] Migration `app/migrations/Version*.php` — chỉ `referral_order.credit_use` + Hasura metadata
- [ ] `app/src/Service/Credit/CreditService.php` — MỚI (availableCredit + deductForPaidOrder)
- [ ] `app/src/Repository/ReferralOrder/ReferralOrderRepository.php` — query SUM(credit_use) theo user + reserve-status
- [ ] `app/src/Service/ReferralOrder/ReferralOrderService.php` — param creditUse + validate/trừ tại applyCrmProductData
- [ ] `app/src/GraphQL/ReferralOrder/Mutation/Create/{Input,Resolver}.php`
- [ ] `app/src/GraphQL/ReferralOrder/Mutation/Update/{Input,Resolver}.php`
- [ ] `app/src/GraphQL/ReferralOrder/Mutation/PreviewOrder/{Input,Resolver}.php`
- [ ] `app/src/GraphQL/ReferralOrder/ReferralOrderEntityType.php` — expose credit_use
- [ ] `app/src/EventSubscriber/Hasura/ReferralOrderPaidSubscriber.php` — dispatch DeductOrderCreditMessage (PTI)
- [ ] `app/src/Message/Credit/DeductOrderCreditMessage.php` + `app/src/MessageHandler/Credit/DeductOrderCreditMessageHandler.php` — MỚI
- [ ] `app/config/packages/messenger.yaml` — route nếu cần
- [ ] Regenerate catalogs: `extract_resolvers.py`, `extract_async_messages.py`

> KHÔNG tạo: User.credit, CreditTransaction entity/repo, migration credit_transaction/user.credit (đã có). KHÔNG dùng status trên credit_transaction. KHÔNG code release (ngầm qua status).

## 12. Thứ tự thực hiện
1. `ReferralOrder.creditUse` + migration + Hasura.
2. `ReferralOrderRepository` SUM query + `CreditService` (availableCredit + deductForPaidOrder).
3. `applyCrmProductData` validate + trừ totalAfterTax.
4. Input + Resolver (Create/Update/Preview) truyền credit_use.
5. Nhánh ==0 mark-paid (§5.1) + dispatch downstream (verify idempotent).
6. `DeductOrderCreditMessage` + handler + cắm vào `ReferralOrderPaidSubscriber` (nhánh >0).
7. Expose credit_use ở entity type.
8. Seed credit thủ công (hoặc tạo DepositOrder thật) → test: preview/create/update giảm total; pay-now ==0 → paid + cộng kho + DEBIT row + −credit; pay-now >0 → charge phần còn lại → CRM paid → DEBIT + −credit; cancel → credit thả; decline → giữ → retry → trừ.

## 13. Out of scope
- Top-up credit (đã có, team khác).
- Refund/reverse credit khi đơn đã paid bị void/cancel (ADR-0005: phantom balance, phase sau).
