# Task: Viết tech spec cho flow Agent purchase products & nhập hàng stock của riêng họ

## Description

Cần bổ sung feature mới, flow như sau:
1. Flow Agent checkout → Nhập stock về warehouse của Agent
2. Tạo new table → `agent_product_stock` : id, agent_id, product_id, quantity, created_at, updated_at
3. Table order `referral_order` add field `type` = “agent_purchase” 
4. Order paid → Add vào `agent_product_stock.quantity`

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