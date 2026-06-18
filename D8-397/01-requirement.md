# Task: Viết tech spec cho flow Agent purchase products & nhập hàng stock của riêng họ

## Description

Cần bổ sung feature mới, flow như sau:
1. Flow Agent checkout → Nhập stock về warehouse của Agent
2. Tạo new table → `agent_product_stock` : id, agent_id, product_id, quantity, created_at, updated_at
3. Table order `referral_order` add field `type` = “agent_purchase” 
4. Order paid → Add vào `agent_product_stock.quantity`
5. Khi tạo order, cho phép nhập sale_price cho từng product, total price tính theo sale_price nếu có truyền, không truyền thì tính như cũ

Tại liệu tham khảo
- docs/domains/referral-order.md
- docs/business/referral-order.md

Hãy Q&A để làm rõ requirement, sau đó viết tech spec, mô tả những gì cần làm để implement những tính năng trên, save ở output/tech-spec.md

## Q&A
> Hỏi đáp để làm rõ requirement

1. **FK của `agent_product_stock`?**
   → `agent_id` = `User.id` (UUID, chính là `referral_order.created_by`). `product_id` = CRM product id dạng **string** (khớp `ReferralOrderProduct.productId`).

2. **Đơn `type = agent_purchase` bỏ qua bước nào so với đơn bán khách thường?**
   → Bỏ **tất cả**: (a) commission (`OrderCommission`), (b) e-sign document → Pay Now thẳng, (c) client info / shipping address, (d) trial / referral.

3. **Cơ chế cộng `agent_product_stock.quantity` khi paid?**
   → Message handler mới, dispatch qua `ReferralOrderPaidSubscriber`, **upsert idempotent** theo `(agent_id, product_id)`.

4. **Thiết kế field `type` trên `referral_order`?**
   → Nullable string + constant `TYPE_AGENT_PURCHASE = 'agent_purchase'`. `null` = đơn bán khách thường (`client_order`). **Không backfill** đơn cũ.

5. **Agent mua hàng có trừ stock kho trung tâm CRM không?**
   → **Không** trừ CRM. Chỉ cộng vào `agent_product_stock`.

6. **API cho flow agent checkout?**
   → Thêm param `type` vào `referral_order_create_mutation` hiện có (tái sử dụng, rẽ nhánh theo type), không tạo mutation riêng.

## Notes
> Note những thông tin quan trọng

- `User` đã có sẵn field `type` với `User::TYPE_AGENT`, và helper `isAgent()`, `isMasterAgent()`, `isFastboyEmployee()`. Chỉ user là agent mới được tạo đơn `agent_purchase`.
- Idempotency là điểm rủi ro cao nhất: cộng stock 2 lần = sai tồn kho. Tech spec đề xuất thêm cờ guard `is_stock_imported` trên `referral_order` + upsert atomic.
- Đơn `agent_purchase` vẫn đi qua payment gateway (thẻ của chính agent) để đạt `paid`; chỉ bỏ bước khách e-sign.
- Tech spec: [output/tech-spec.md](output/tech-spec.md).

## Cập nhật — pivot sang `resell_type` (sau phase 1)

Field `type`/`agent_purchase` ở Q&A trên đã được thay bằng **`resell_type`** (enum string), theo [phase1/01-requirement.md](phase1/01-requirement.md):

- `sell_via_crm` (default, ≡ `client_order` cũ) · `purchase_to_inventory` (≡ `agent_purchase` cũ) · `sell_from_inventory` (tương lai, FE chưa truyền được).
- FE chỉ truyền `RESELL_TYPES_SUPPORTED` = {`sell_via_crm`, `purchase_to_inventory`}.

**Phase 1 (ĐÃ XONG, commit `62737109`):** field `resell_type` + index, Input/Resolver/Service/EntityType, default `sell_via_crm`, expose qua Hasura, và **CRM sync** (`resellType` gửi sang CRM khi paid).

**Phase 2 (tech spec mô tả):** bảng `agent_product_stock` + `stock_imported_at` + strip-down flow + cộng kho khi paid.

**Quyết định bổ sung (Q&A vòng 3 — đảo strip-down):**
1. `purchase_to_inventory` tạo **y hệt đơn thường** — **KHÔNG strip-down**. Vẫn client/company, shipping, e-sign, trừ CRM stock, tax/shipping, pay-now tùy chọn. (Khớp curl dev — FE không phải đổi payload.)
2. Khác biệt **chỉ khi paid**: **bỏ** commission (`OrderCommission` + `ChangeUserAmount`) + trial; **giữ** PDF + email + pay slip + CRM-noti; **thêm** cộng `agent_product_stock`.
3. Idempotency: thêm cột guard `stock_imported_at` + upsert atomic.
4. **BE** cộng `agent_product_stock` (source of truth); CRM chỉ nhận `resellType`.
5. (Open) Permission: có giới hạn chỉ `isAgent()` tạo được `purchase_to_inventory` không.