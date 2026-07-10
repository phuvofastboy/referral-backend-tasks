> **STATUS: DONE** — verify: container compile OK, SDL build OK (exit 0, `delivery_status` biến mất, mutation `referral_order_order_delivered_mutation` + `issue_items` mới), migration applied, schema:validate OK, DQL/SQL (existsByWarehouseIssue, countAvailableByReseller, insertFromIssue) hợp lệ, messenger sạch (AddAgentProductStock/OrderDeliveredMessage đã xóa), catalogs regenerated. Deploy: `hasura metadata apply` (đã gỡ trigger ReferralOrderDelivered).

## Description
- Bỏ field referral_order.delivery_status. Do order có thể delivery 1 phần, không phải toàn bộ order
- api OrderDelivered update lại, truyền issue_id, referral_order_id, chi tiết issue_items, serial_number của từng item
- Sử dụng data này để insert vào reseller_inventory_product_item và count lại agent_product_stock

## Q&A — Đã chốt

### Bỏ delivery_status (revert task 02 + gỡ event flow task 04)
- Bỏ field `ReferralOrder.deliveryStatus` (property + getter/setter) → migration **DROP COLUMN delivery_status**.
- Bỏ GraphQL SourceField `delivery_status` (ReferralOrderEntityType).
- Xóa enum `TrackingStatus` (sau khi gỡ hết usage).
- Bỏ `ReferralOrderService::updateDeliveryStatus`.
- **Gỡ hẳn event flow task 04**: Hasura trigger `ReferralOrderDelivered` (metadata yaml), `ReferralOrderDeliveredSubscriber`, `OrderDeliveredMessage`, `OrderDeliveredMessageHandler`, `ResellerInventorySerialAssigner` (seam).

### Timing tạo item: chuyển paid → delivered
- **Gỡ hẳn inventory flow ở PAID**: không dispatch `AddAgentProductStockMessage` cho purchase_to_inventory nữa. Xóa `AddAgentProductStockMessage` + handler + `AgentProductStockService::importFromPurchaseOrder` + `ResellerInventoryProductItemRepository::insertItemsForOrder` (batch-by-quantity, serial null).
- `stockImportedAt` chỉ dùng trong AddAgentProductStockMessageHandler (đang xóa) → **bỏ luôn** property + getter/setter → migration **DROP stock_imported_at**.

### OrderDelivered mới (sync, CRM gọi)
- Mutation `ROLE_HASURA_CRM`, `#[Transactional]`, output `Boolean!`. **Sync** — insert + recount ngay trong mutation (không async).
- Input: `warehouse_issue_id` (uuid), `referral_order_id` (uuid, EntityExist), `issue_items: [{ product_id (uuid), serial_number (string) }]` — mỗi entry = 1 unit.
- Logic:
  1. **Idempotency**: nếu đã có row `reseller_inventory_product_item` với `warehouse_issue_id` này → return (skip, CRM retry an toàn).
  2. **Validate**: order là purchase_to_inventory; mỗi `issue_item.product_id` ∈ products của order; serial_number không rỗng.
  3. **Insert** mỗi issue_item thành 1 `reseller_inventory_product_item` (reseller = order.createdBy, product_id, serial_number, purchase_order = order, **warehouse_issue_id**, sell_order = null).
  4. **Recount** `agent_product_stock` (`AgentProductStockService::recomputeAvailableStock`).

### Schema
- `reseller_inventory_product_item`: **thêm cột `warehouse_issue_id`** (uuid, nullable — item cũ không có) + index (idempotency lookup).
- `referral_order`: **drop `delivery_status`**.
- 1 migration cho cả hai (doctrine:migration:diff).

### Availability gate (đơn giản hóa — bỏ delivery_status)
- `countAvailableByReseller`: bỏ join `purchase_order.delivery_status`; điều kiện = **`sell_order_id IS NULL` + `serial_number IS NOT NULL`**.
- `validateProductSerials` (task 03 Part B): bỏ check delivered; giữ **serial != null + chưa bán (sell_order_id null)** + reserved + ownership + product match.

### Giữ nguyên (còn hợp lệ)
- Task 03 Part B (nested serials, reconcile), Part B4 (`MarkResellerInventorySoldMessage` set sell_order_id khi sell paid), `AgentProductStockService::recomputeAvailableStock` + `upsertSetBatch`, `markSoldByOrder`.