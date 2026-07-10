# Task: Event order delivered

> **STATUS: SCAFFOLD DONE** — verify: container compile OK, SDL build OK (mutation `referral_order_order_delivered_mutation`), messenger handler wired, DQL `countAvailableByReseller` (join + enum) chạy OK. **Seam serial (`ResellerInventorySerialAssigner::assignFromCrm`) là TODO chờ spec API CRM.** Deploy cần **`hasura metadata apply`** để tạo trigger `ReferralOrderDelivered`.

## Description
- Cần viết mutation để handle event order delivered (để CRM sẽ call vào)

Input: 
- referral_order_id - uuid
- delivery_status - string
Logic:
- Xử lý update delivery_status theo referral_order_id

- Sau đó viết subscriber và handler cho event order delivered bên referral
- trong handler, xử lý:
  - Xử lý gán serial_number cho reseller_product_item
  - kiểm tra và đảm bảo rằng product_item này ready để có thể select khi create/update order

## Q&A — Đã chốt

1. **Order type**: event delivered chỉ xử lý cho **purchase_to_inventory** (reseller nhập kho). Sell order có thể cũng nhận delivery_status nhưng subscriber/handler chỉ act cho purchase_to_inventory.

2. **Mutation** (CRM gọi vào):
   - `#[Roles('ROLE_HASURA_CRM')]`, `#[Transactional]`, output `Boolean!`.
   - Input: `referral_order_id` (uuid, EntityExist), `delivery_status` (string, validate ∈ `TrackingStatus`).
   - **Update trực tiếp** `referral_order.delivery_status` (không dispatch message). Logic ở service (resolver mỏng).
   - Hasura event trigger **`ReferralOrderDelivered`** watch cột `delivery_status` (thêm vào `hasura/metadata/.../public_referral_order.yaml`, webhook `SYMFONY_EVENT_TRIGGER_ENDPOINT`) → cần `hasura metadata apply`.

3. **Subscriber + handler** (`ReferralOrderDeliveredSubscriber` → `OrderDeliveredMessage` → handler):
   - Subscriber act khi `new.delivery_status == delivered` (và old != delivered) và `resell_type == purchase_to_inventory`.
   - Handler: (a) **fetch serial qua CRM API** rồi gán `serial_number` cho `reseller_inventory_product_item` của purchase order; (b) recompute AgentProductStock; idempotent.

4. **Nguồn serial_number**: KHÔNG nằm trong mutation input. Referral sẽ **gọi 1 API qua CRM để fetch serial theo order** — **spec API mô tả sau**. Task này **scaffold + chừa seam** rõ ràng (service method + TODO), chưa nối CRM thật.

5. **"ready để select"** = **gate availability theo delivered** (REVISE task 03):
   - Điều kiện available/selectable của 1 product_item: `sell_order_id IS NULL` **AND** purchase_order `delivery_status = 'delivered'` **AND** `serial_number != NULL`.
   - `countAvailableByReseller`: giữ gọi recompute ở **paid** (task 03) nhưng **thêm filter** → lúc paid tự = 0 (chưa delivered/serial), delivered handler gọi lại recompute.
   - Task 03 Part B `validateProductSerials`: thêm gate — item được chọn phải thoả điều kiện available ở trên (delivered + serial + chưa bán).

### Scope task 04 (scaffold tất cả, chừa seam CRM fetch)
- Mutation `OrderDelivered/{Input,Resolver}` + `ReferralOrderService::updateDeliveryStatus`.
- Hasura trigger `ReferralOrderDelivered` (metadata yaml).
- `ReferralOrderDeliveredSubscriber` + `OrderDeliveredMessage` + `OrderDeliveredMessageHandler`.
- Seam: `ResellerInventorySerialAssigner::assignFromCrm(order)` — TODO chờ spec API.
- Repo: sửa `countAvailableByReseller` (join purchase_order delivered + serial not null). Sửa `validateProductSerials` (gate available).
- `TrackingStatus::values()` cho Assert\Choice.

### Lưu ý (báo trước)
- Trước khi seam CRM fetch serial được nối, item delivered vẫn **chưa có serial → chưa available** (đúng thiết kế gate). Availability chỉ "bật" sau khi serial được gán.
- `upsertSetBatch` chỉ set product có trong map (count>0); product tụt về 0 available sẽ không tự bị zero — hạn chế sẵn có từ task 03, chưa fix ở task này trừ khi yêu cầu.