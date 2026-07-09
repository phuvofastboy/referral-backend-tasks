# D8-426/07 — Plan: use credit cho order `sell_via_fastboy` (= `sell_via_crm`)

> Yêu cầu + Q&A đã chốt: [`01-task-description.md`](01-task-description.md).
> Mục tiêu: cho phép `credit_use` trên đơn `sell_via_crm`, và cộng đúng `credit_use` vào `creatorCommissionAmount`. Reuse toàn bộ hạ tầng credit của PTI (D8-426).

## 1. Nguyên tắc

- **Không thêm resell_type/enum/migration/Hasura.** `sell_via_fastboy` = `RESELL_TYPE_SELL_VIA_CRM`.
- Chỉ **nới các gate** đang khoá credit ở `purchase_to_inventory` để nhận thêm `sell_via_crm`, và thêm **commission add-back**.
- Credit flow (reserve qua status → deduct `user.credit` khi paid → `credit_transaction(debit, use_credit_for_order)` → idempotency `credit_deducted_at`) **giữ nguyên**, đã type-agnostic.

## 2. Thay đổi code

### 2.1 Helper phân loại — `ReferralOrder`
Thêm method gom điều kiện "được dùng credit" để tránh lặp `isPurchaseToInventory() || sell_via_crm` ở nhiều nơi:
```php
public function isCreditUsable(): bool
{
    return $this->isPurchaseToInventory()
        || $this->getEffectiveResellType() === self::RESELL_TYPE_SELL_VIA_CRM;
}
```

### 2.2 Nới gate validate — `ReferralOrderService::applyCreditUse` (~line 615)
```php
// cũ: if (!$order->isPurchaseToInventory()) throw ...'purchase_to_inventory orders'
if (!$order->isCreditUsable()) {
    throw new GraphQLException('Credit can only be used for purchase_to_inventory or sell_via_crm orders');
}
```
Giữ nguyên 2 check còn lại: `credit_use ≤ grossAfterTax` và `credit_use ≤ availableCredit(creator)`.

### 2.3 Commission add-back — `ReferralOrderService::applyCrmProductData`
Commission gốc vẫn tính ở block `$isSubmit` hiện tại (line ~575-591; nhánh sell_via_crm set `creatorCommissionAmount = base`). **Sau** khi `applyCreditUse` chạy xong (line ~595 `setTotalAfterTax(...)`), thêm:
```php
if ($isSubmit
    && $order->getEffectiveResellType() === ReferralOrder::RESELL_TYPE_SELL_VIA_CRM
    && ($creditUse = round((float) $order->getCreditUse(), 2)) > 0.0
) {
    $order->setCreatorCommissionAmount(
        round((float) $order->getCreatorCommissionAmount() + $creditUse, 2),
    );
}
```
- Đặt sau `applyCreditUse` để chỉ cộng khi credit_use đã validate hợp lệ (nếu invalid → applyCreditUse throw → rollback).
- Áp cho cả markup lẫn percentage vì `creatorCommissionAmount` đã được set đúng `base` trong cả hai nhánh.

### 2.4 Deduct hook khi paid — `ReferralOrderPaidSubscriber` (line ~126-131)
Hiện `DeductOrderCreditMessage` chỉ dispatch trong nhánh PTI. Tách phần deduct-credit ra khỏi nhánh PTI để chạy cho **cả** PTI và sell_via_crm; giữ `AddAgentProductStockMessage` **chỉ** trong nhánh PTI:
```php
if ($resellType === ReferralOrder::RESELL_TYPE_PURCHASE_TO_INVENTORY) {
    $this->bus->dispatch(new AddAgentProductStockMessage($newData['id']));
}
// credit debit cho mọi loại được dùng credit (PTI + sell_via_crm)
if (
    in_array($resellType, [ReferralOrder::RESELL_TYPE_PURCHASE_TO_INVENTORY, ReferralOrder::RESELL_TYPE_SELL_VIA_CRM], true)
    && (float) ($newData['credit_use'] ?? 0) > 0
) {
    $this->bus->dispatch(new DeductOrderCreditMessage($newData['id']));
}
```

### 2.5 Handler re-check — `DeductOrderCreditMessageHandler`
```php
// cũ: if (!$order->isPurchaseToInventory() || status !== PAID) return;
if (!$order->isCreditUsable() || $order->getStatus() !== ReferralOrder::STATUS_PAID) {
    return;
}
```

### 2.6 Nhánh full-cover (`total_after_tax == 0`) — Create/Update resolver
**Create** (`Mutation/Create/Resolver.php`):
- Guard `$isCreditFullyPaid` (line ~213-217): đổi `->isPurchaseToInventory()` → `->isCreditUsable()`.
- Block dispatch (line ~262-274): `AddAgentProductStockMessage` **chỉ** khi `$order->isPurchaseToInventory()`; `OrderPaidMessage` + `DeductOrderCreditMessage` cho cả hai:
```php
if ($isCreditFullyPaid) {
    $this->entityManager->refresh($order);
    $orderId = $order->getId()->__toString();
    if ($order->isPurchaseToInventory()) {
        $this->bus->dispatch(new AddAgentProductStockMessage($orderId));
    }
    $this->bus->dispatch(new OrderPaidMessage($orderId, (string) $order->getInternalId(), ReferralOrder::STATUS_PAID, $order->getEffectiveResellType()));
    $this->bus->dispatch(new DeductOrderCreditMessage($orderId));
}
```
**Update** (`Mutation/Update/Resolver.php`, line ~255): đổi `->isPurchaseToInventory()` → `->isCreditUsable()`. Update là UPDATE thật → fire trigger `ReferralOrderPaid` → subscriber (2.4) lo deduct; kiểm tra không double-dispatch (giữ nguyên hành vi hiện tại của nhánh này).

## 3. KHÔNG cần đụng
- `CreditService` (deduct/available), `sumReservedCreditUse`, `credit_transaction`, migration, Hasura metadata — đều type-agnostic hoặc không liên quan.
- Input `credit_use` đã có sẵn ở Create/Update/PreviewOrder + expose ở EntityType.
- Reserve/available: `sumReservedCreditUse` lọc theo `credit_use>0` + status (không theo resell_type) → đơn sell_via_crm tự động reserve, không cần sửa.

## 4. Edge cases / rủi ro cần lưu ý
- **Commission gốc tính trên gross** (trước credit): `base = rate × totalMainProduct` không đổi khi giảm giá bằng credit; rồi `+ credit_use`. Đúng ý "base + credit_use".
- **`sell_via_crm` + PTI phân biệt downstream**: chỉ PTI nhập kho (AddAgentProductStock). Đảm bảo mọi nhánh mark-paid/hook đều gate AddAgentProductStock theo PTI.
- **Markup user**: `creatorCommissionAmount` (base) có thể = markup total; add-back vẫn cộng thẳng credit_use — xác nhận business chấp nhận (đã chốt Q4: áp dụng cả hai).
- **`credit_use ≤ gross` với sell_via_crm**: client trả remainder; nếu credit ≥ gross → nhánh full-cover (2.6). Cap `≤ gross` giữ nguyên, không cho âm.
- **Reserve khi chưa paid**: đơn sell_via_crm có credit_use ở reserve-status sẽ giữ credit của reseller — nhất quán PTI.
- **Refund/void sau paid**: vẫn out-of-scope (ADR-0005 phantom balance) — commission đã cộng + credit đã trừ, chưa có reverse. Ghi nhận, không xử lý ở task này.

## 5. Verify sau implement
- `php -l` các file đổi; `doctrine:schema:validate` (không đổi schema nhưng chạy để chắc); `graphqlite:dump-schema` (không field mới).
- E2E (skill smoke-test-graphql-api): tạo đơn `sell_via_crm` có `credit_use` → `total_after_tax` giảm đúng; `creator_commission_amount = base + credit_use`; reserve giảm `available_credit`; paid → `user.credit` trừ + `credit_transaction(debit, use_credit_for_order)`; full-cover → PAID ngay, **không** AddAgentProductStock; redeliver không trừ đôi.
- Validate reject: credit_use > available / > gross; dùng credit trên resell_type không phải {PTI, sell_via_crm}.
