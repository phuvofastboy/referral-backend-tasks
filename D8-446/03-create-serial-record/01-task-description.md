## Description
- Khi paid order "purchase_to_inventory" 
  - Cần tạo record cho table `reseller_inventory_product_item` 
  - purchase_order_id -> assign referral_order_id được paid
  - sell_order_id -> để null as default

- Khi create/update/preview order "sell_from_inventory"
  - cho phép truyền list `referral_order_product_serials` -> sẽ replace vào data hiện tại
  - Lưu ý là phải xử lý check `product_item_id` đang được reserved ở order khác chưa, nếu đã reserved thì không cho add vào order

## Q&A — Đã chốt

### Part A — purchase_to_inventory paid → tạo reseller_inventory_product_item
1. **Số row/line**: quantity = N → tạo **N row** (1 row / 1 item vật lý), `serial_number = null` lúc tạo. Bảng không có cột quantity.
2. **Field mỗi row**: `reseller_id = order.createdBy`, `product_id = product.productId`, `purchase_order_id = order (paid)`, `sell_order_id = null`, `serial_number = null`.
3. **Hook**: mở rộng flow hiện có — `AddAgentProductStockMessageHandler` + `AgentProductStockService::increaseFromOrder`. Tái dùng guard idempotency `stockImportedAt`. Bỏ qua shipping product.
4. **Quan hệ với AgentProductStock (ĐỔI LOGIC)**: giữ cả 2 bảng. Không còn increment trực tiếp. Flow mới:
   - Tạo N per-item rows vào `reseller_inventory_product_item`.
   - **Count** `reseller_inventory_product_item` theo `(reseller, product)` với `sell_order_id IS NULL` → **set** (không increment) quantity vào `AgentProductStock`.

### Part B — sell_from_inventory create/update/preview → referral_order_product_serials
5. **Input** `referral_order_product_serials`: list, mỗi entry = `{ product_id (CRM, map về đúng product line), product_item_id, serial_number? }`. Thêm vào Create/Update/PreviewOrder Input.
6. **Replace semantics**: xóa toàn bộ `referral_order_product_serial` hiện có của order → tạo lại từ list truyền lên.
7. **Reserved check**: product_item bị reserved nếu **đang được tham chiếu bởi `referral_order_product_serial` thuộc order KHÁC** mà order đó chưa cancelled/rejected (bao gồm cả draft/pending). Nếu reserved → reject, không cho add. Loại trừ chính order đang thao tác.
8. **sell_order_id**: KHÔNG set ở bước assign serial. Chỉ set khi **sell order chuyển paid** (item rời kho thật sự) — đối xứng Part A.
9. **Preview**: validate-only (reserved check + tính toán), KHÔNG persist serial.

### Scope task 03
- (A) Sửa `AgentProductStockService`/`AddAgentProductStockMessageHandler`: tạo per-item + recompute AgentProductStock từ count.
- (B) Input `referral_order_product_serials` + xử lý replace + reserved check ở Create/Update/PreviewOrder; repository query reserved + count.