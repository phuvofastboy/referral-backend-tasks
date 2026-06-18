# Requirement #5 — sale_price per product (note)

> Nguồn: [01-requirement.md](../01-requirement.md) mục #5.
> "Khi tạo order, cho phép nhập `sale_price` cho từng product; total tính theo `sale_price` nếu có truyền, không truyền thì tính như cũ."
>
> Concern **độc lập** với feature agent-stock — nên là 1 phase/task riêng.

---

## A. Logic tính `total` hiện tại (`ReferralOrderService::applyCrmProductData`, `app/src/Service/ReferralOrder/ReferralOrderService.php`)

**Mỗi product:**

1. **`unitPrice`** (`:322-334`) — từ CRM theo role:
   - insider (Fastboy agent / parent là Fastboy master) → `priceForAgentAndMaster`
   - reseller thường → `priceForReseller`
   - không có → 0 (submit thì throw)
2. **`salePrice`** (`:336-347`) — **hiện chỉ áp dụng khi** `isParentFastboyAgent && card_type === customer_card`:
   - trong điều kiện: null → mặc định = unitPrice; bắt buộc `>= unitPrice` (`isValidSalePrice`)
   - ngoài điều kiện → **ép `salePrice = null`** (bỏ giá trị FE gửi)
3. **`priceUsed = salePrice ?? unitPrice`** (`:355`)
4. **`total` (per product) = `priceUsed * quantity`** (`:357`); `totalBeforeDiscount = totalAfterDiscount = total`
5. **`unitPriceTotal = unitPrice * quantity`** (`:358`) — giá vốn
6. **Tax** (`:363-374`): Fastboy master → 0; Avalara → theo dòng; rate → `total * taxRate/100`
7. **`totalAfterTax` (product) = `total + productTaxPrice`** (`:385`)
8. **Commission markup** (`:388-390`): chỉ Fastboy parent → `totalCommission += (total − unitPriceTotal)`

**Cộng dồn order (`:456-471`):**
- `order.total` = Σ `total` (Σ priceUsed×qty), **không** gồm shipping (logic mới)
- `order.totalTax` = Σ productTax + shippingTax (+ avalara shipping/processing)
- `order.totalAfterTax` = `total + totalTax + shippingFee`
- shipping fee để riêng (`totalShippingFee*`)

**Commission order-level (submit, `:483-487`):**
- Fastboy parent/insider → `commissionAmount = totalCommission` (markup), rate = null
- reseller thường → `commissionAmount = totalMainProduct × commissionRate`

→ Tóm: **`total = Σ (salePrice ?? unitPrice) × qty`**, nhưng `salePrice` hiện chỉ "sống" trong case Fastboy-parent + customer_card; case khác bị ép null → total = unitPrice × qty.

---

## B. Quyết định (Q&A vòng này)

1. **Phạm vi**: mọi user, **chỉ đơn `sell_via_crm`** (bỏ gate card_type/Fastboy). **KHÔNG** áp cho `purchase_to_inventory` (dùng insider price).
2. **Commission**: **giữ nguyên logic hiện tại** — total tăng theo sale_price → commission reseller tăng theo rate; Fastboy parent vẫn markup.
3. **Validation**: giữ ràng buộc **`sale_price >= unit_price`** (`isValidSalePrice`).

---

## C. Scope thay đổi (KHÔNG cần field/migration mới — `sale_price` đã tồn tại)

### C1. `applyCrmProductData` — sửa gate ở **2 chỗ** (`:287-298` builder Avalara + `:336-347` vòng chính)

Thay:
```php
if ($isParentFastboyAgent && $order->getCardType() === ReferralOrder::CUSTOMER_CARD) { ... }
else { $salePrice = null; }
```
bằng (khuyến nghị tách helper `resolveSalePrice($order, $product, $unitPrice)` dùng chung 2 chỗ):
```php
$salePrice = $product->salePrice;
if ($order->getEffectiveResellType() === ReferralOrder::RESELL_TYPE_SELL_VIA_CRM && $salePrice !== null) {
    if (!$this->orderProductService->isValidSalePrice($salePrice, $unitPrice)) {
        throw new GraphQLException("Sale price must be rather than or equal to unit price");
    }
} else {
    $salePrice = null; // purchase_to_inventory hoặc không truyền → giá vốn như cũ
}
```
`priceUsed = salePrice ?? unitPrice` → total/tax/totalAfterTax tự ăn theo (đã sẵn).

### C2. Commission — KHÔNG đổi code
- ⚠ Hệ quả bỏ gate: Fastboy parent giờ dùng sale_price cả trên `agent_card` (trước chỉ customer_card) → có markup. Đúng theo "bỏ gate card_type".

### C3. `isUsingSalePrice` (`:1101` `toArrayForEmail`) — cập nhật
Từ `$isParentFastboyAgent && $isCustomerCardType` → kiểm tra đơn có product nào `salePrice !== null`:
```php
'isUsingSalePrice' => $referralOrder->getReferralOrderProducts()->exists(
    fn($k, $p) => $p->getSalePrice() !== null
),
```

### C4. KHÔNG cần
- Field/migration mới (cột `sale_price` đã có trên `ReferralOrderProduct`).
- Input mới (`sale_price` + `Assert\Positive` đã có trong `ReferralOrderProductInput`).

### C5. Lưu ý
- `applyCrmProductData` dùng chung create/update/preview → sửa 1 chỗ cover hết.

---

## D. Test sau khi sửa

- Reseller thường, `sell_via_crm`, `sale_price > unit` → `total` theo sale_price; commission tăng theo rate.
- Không truyền `sale_price` → total = unit như cũ.
- `sale_price < unit_price` → reject ("Sale price must be ...").
- `purchase_to_inventory` → bỏ qua sale_price (dùng insider price).

---

## E. Câu hỏi mở / chưa quyết

- Có viết requirement #5 vào `output/tech-spec.md` (mục riêng) không, hay chỉ note này đủ?
- Thêm thành Phase 7 trong `02-plan.md` (độc lập, có thể làm trước/song song agent-stock)?
