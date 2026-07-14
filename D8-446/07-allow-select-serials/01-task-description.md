## Description
- API create/update/preview order, cần cho phép chọn serials (chọn từ reseller_inventory_product_item)
- khi pay order thì phải validate qty trong order, phải khớp với qty serials được select
- chỉ được select những product item available, select item reserved ở order khác rồi thì báo lỗi
- khi order paid, thì update lại table product_item, update sell_order_id, để biết là đã xuất kho

## Q&A (đã chốt)

Bản chất task = **bật lại** serial flow đã tạm tắt ở task 05 + **validate lại** qty khớp serials.
Hạ tầng đã có sẵn từ task 03: `ReferralOrderProductSerialService::validateProductSerials()` +
`syncFromProducts()`, reserved-item check (`findReservedProductItemIds`), markSold on paid
(`MarkResellerInventorySoldMessage` → `markSoldByOrder`). Chỉ cần gỡ comment + thêm strict qty.

1. **Điểm validate "count(serials) == quantity"**: fire khi **submit** (`status === STATUS_SENT`,
   biến `$isSubmitOrder` trong Create/Update). Đây là cổng đồng bộ duy nhất trước khi charge —
   paid luôn kéo theo submit. Async paid event (`ReferralOrderPaidSubscriber`) chỉ markSold, không
   validate lại (tiền đã charge, không reject được).
2. **Draft (chưa submit)**: cho chọn thiếu serials — `count <= quantity` (lưu tiến độ). Chỉ enforce
   `count === quantity` khi `$isSubmitOrder`. Preview luôn soft (`count <= quantity`).
3. **Scope**: chỉ `sell_from_inventory`. `purchase_to_inventory` nhận serial từ CRM lúc giao hàng
   (mutation `referral_order_order_delivered_mutation`), không do user chọn ở order.
4. **markSold khi paid**: giữ nguyên wiring hiện có (`ReferralOrderPaidSubscriber` →
   `markSoldByOrder` set `sell_order_id`). Không đổi.

### Cách làm
- Bỏ comment field `serials` trong `ReferralOrderProductInput`.
- Thêm param `bool $requireExactQuantity = false` cho `validateProductSerials()` + `syncFromProducts()`.
  Strict → mỗi non-shipping line `count === quantity`; soft → `count <= quantity`.
- Create/Update: gỡ comment `syncFromProducts(..., $isSubmitOrder)`.
- Preview: gỡ comment `validateProductSerials(...)` (soft, default false).

## Q&A (gốc)