# Task: Update rule to use credit

## Description

DB cần thay đổi:
- New table `reseller_inventory_product_item`
    - id - uuid, PK
    - reseller_id - uuid, FK -> user
    - product_id - uuid
    - serial_number - string, nullable
    - purchase_order_id - uuid, FK -> referral_order
    - sell_order_id - uuid, FK -> referral_order
    - created_at - timestamp
    - updated_at - timestamp
- New table `referral_order_product_serial`
    - id - uuid, PK
    - referral_order_product_id - uuid, FK -> referral_order_product
    - product_item_id - uuid, FK -> reseller_inventory_product_item
    - serial_number - string, nullable
    - created_at - timestamp
    - updated_at - timestamp
  
- Hãy dùng php doctrine:migration:diff để tạo file migration

## Q&A — Đã chốt

**Convention tham chiếu:** `App\Entity\Stock\AgentProductStock` (kho theo reseller/agent) — UUID v7 PK, `#[Gedmo\Timestampable]`, FK → User onDelete CASCADE.

1. **`product_id` = `string`** (KHÔNG phải uuid như mô tả gốc). Toàn bộ codebase lưu `productId` là string (CRM product id): `AgentProductStock.productId`, `ReferralOrderProduct.productId`.
2. **Nullability order FK** trên `reseller_inventory_product_item`: `purchase_order_id` **NOT NULL** (item luôn sinh từ 1 purchase order), `sell_order_id` **nullable** (null cho tới khi bán ra).
3. **onDelete**: `reseller_id` → **CASCADE** (giống AgentProductStock.agent); các FK order (`purchase_order_id`, `sell_order_id`) → **SET NULL** (giữ lịch sử inventory khi order bị xóa).
4. **serial_number** giữ ở CẢ hai bảng: trên `reseller_inventory_product_item` là **nguồn thật**; trên `referral_order_product_serial` là **snapshot** tại thời điểm bán. Cả hai nullable.
5. **Unique constraint** trên `reseller_inventory_product_item`: **`(product_id, serial_number)`** (Postgres cho phép nhiều NULL → item chưa có serial vẫn insert được). Kèm **index đơn `serial_number`** trên cả hai bảng để tra cứu theo serial (composite unique không phục vụ query chỉ lọc serial vì cột dẫn đầu là product_id).
6. **Namespace**: cả hai entity đặt trong `App\Entity\Stock\` (gần `AgentProductStock`).
   - `reseller_inventory_product_item` → `ResellerInventoryProductItem`
   - `referral_order_product_serial` → `ReferralOrderProductSerial`
7. **`referral_order_product_serial` FK** (task không nêu → mặc định): `referral_order_product_id` **NOT NULL onDelete CASCADE**, `product_item_id` **NOT NULL onDelete CASCADE**.

**Scope sub-task 01:** 2 Entity + 2 Repository + migration (`doctrine:migration:diff`, review trước khi apply).