# Task: Use credit cho order "sell_via_fastboy"

## Description
- Tôi cần bổ sung logic: cho phép apply credit_use cho order với resell_type là "sell_via_fastboy"
- Khi use credit cho order này thì yêu cầu là số credit sẽ được cộng lại vào commission
- Q&A để clear requirement, sau đó viết plan -> 02-plan.md

## Q&A

**Đã chốt (2026-07-01):**

1. **`sell_via_fastboy` = alias của `sell_via_crm`** (không thêm resell_type mới, không migration/enum). "Bán qua Fastboy" chính là `RESELL_TYPE_SELL_VIA_CRM` hiện có → chỉ nới các gate đang giới hạn credit ở `purchase_to_inventory`.

2. **Luồng tiền:** `credit_use` trừ vào `total_after_tax` (payer/client trả phần còn lại qua gateway). Reseller bị trừ `user.credit` khi đơn paid, và được **cộng lại đúng `credit_use` vào `creatorCommissionAmount`**. Net: reseller chuyển credit → commission; client được giảm giá bằng credit của reseller.

3. **Commission add-back:** cộng vào `creatorCommissionAmount` **trên order** (không phải `resellAmount`). Công thức: `creatorCommissionAmount = base + credit_use`, trong đó `base` = commission gốc như sell_via_crm hiện tại (rate × totalMainProduct, hoặc markup total).

4. **Áp dụng cho CẢ markup user lẫn percentage-commission user** (mọi reseller sell_via_crm).

5. **Reuse y hệt model PTI:** reserve ngầm qua `status`; deduct `user.credit` khi paid + ghi `credit_transaction(direction=debit, type=use_credit_for_order)`; validate `credit_use ≤ available_credit` **và** `≤ gross total_after_tax`.

6. **Full-cover (`total_after_tax == 0`):** mark PAID ngay như PTI (bypass transition, INSERT không fire trigger → dispatch downstream thủ công). Khác PTI: **KHÔNG** dispatch `AddAgentProductStockMessage` (logic nhập kho reseller, chỉ cho PTI).

7. **Timing:** add-back commission tính lúc **submit** (cùng chỗ set `creatorCommissionAmount` trong `applyCrmProductData`), sau khi `applyCreditUse` validate xong.