# Plan — D8-446 / 03: Serial-based reseller inventory records

> **STATUS: DONE** — implement xong, verify: container compile OK, GraphQL SDL build OK (exit 0), `doctrine:schema:validate` OK, EXPLAIN 6 query repo hợp lệ, messenger handler wired. Cần `composer dump-autoload` cho class mới (autoloader authoritative — theo memory `graphqlite-new-resolver-gotchas`).


> Prereq: task 01 (tables `reseller_inventory_product_item`, `referral_order_product_serial` + entities/repos) đã xong. Task này chỉ code, **không migration mới**.

Nguồn quyết định: `01-task-description.md` §Q&A.

---

## Part A — purchase_to_inventory paid → tạo per-item + recompute AgentProductStock

### Flow hiện tại
`ReferralOrderPaidSubscriber` (hoặc Create resolver khi credit-fully-paid) dispatch `AddAgentProductStockMessage`
→ `AddAgentProductStockMessageHandler::__invoke` (guard `isPurchaseToInventory` + `status=paid` + `stockImportedAt === null`, trong 1 transaction)
→ `AgentProductStockService::increaseFromOrder($order)` → `AgentProductStockRepository::upsertIncrementBatch` (increment).

### Thay đổi

**1. `ResellerInventoryProductItemRepository` — thêm 2 method**
- `insertItemsForOrder(Uuid $resellerId, Uuid $purchaseOrderId, array $quantitiesByProductId): void`
  Batch INSERT N row/product (id=uuid v7, reseller_id, product_id, purchase_order_id, sell_order_id=null, serial_number=null, created_at/updated_at=NOW()). Mẫu theo `AgentProductStockRepository::upsertIncrementBatch` (raw SQL, multi-row VALUES).
- `countAvailableByReseller(Uuid $resellerId): array` → `[productId => count]` với `sell_order_id IS NULL`, group by product_id. (Trả full map để set lại AgentProductStock.)

**2. `AgentProductStockRepository` — thêm method set-from-count**
- `upsertSetBatch(Uuid $agentId, array $quantitiesByProductId): void`
  Giống `upsertIncrementBatch` nhưng `ON CONFLICT (agent_id, product_id) DO UPDATE SET quantity = EXCLUDED.quantity` (SET, không cộng dồn). Giữ `upsertIncrementBatch` cũ nguyên vẹn (không phá caller khác — kiểm tra không còn caller nào khác trước khi quyết định).

**3. `AgentProductStockService::increaseFromOrder` — đổi logic** (đổi tên → `importFromPurchaseOrder` cho đúng nghĩa, giữ signature `ReferralOrder`)
   1. Gom `quantitiesByProductId` từ non-shipping products (như cũ).
   2. `resellerInventoryRepo->insertItemsForOrder(reseller, order, quantitiesByProductId)` — tạo N per-item rows.
   3. `$counts = resellerInventoryRepo->countAvailableByReseller(reseller)`.
   4. `agentProductStockRepo->upsertSetBatch(reseller, $counts)`.
   - Inject `ResellerInventoryProductItemRepository` vào service.
   - Cập nhật tên method gọi trong `AddAgentProductStockMessageHandler` nếu đổi tên.

**Idempotency**: guard `stockImportedAt` ở handler đã chống double. INSERT per-item chỉ chạy 1 lần/order. (Set-from-count tự nhất quán kể cả chạy lại — nhưng vẫn để guard.)

**Không đụng**: logic trừ tồn `sell_from_inventory` (`deductForSubmit`/`AgentProductStockRepository::deductQuantity`) giữ nguyên.

---

## Part B — sell_from_inventory create/update/preview → referral_order_product_serials

### B1. Input mới
Tạo `App\GraphQL\ReferralOrder\ReferralOrderProductSerialInput` (`#[GraphQL\Input name: 'referral_order_product_serial_input']`):
- `product_id: String` — CRM product id, map về đúng product line (NotBlank + Uuid... theo convention productId là uuid string? — productId trong ReferralOrderProductInput validate `Uuid`; theo đó).
- `product_item_id: String` — Uuid + EntityExist(ResellerInventoryProductItem).
- `serial_number: String` (nullable) — optional override; mặc định lấy từ product_item.serialNumber.

Thêm field vào 3 Input:
- `Create/Input.php`, `Update/Input.php`, `PreviewOrder/Input.php`:
  `#[GraphQL\Field(name: 'referral_order_product_serials', inputType: '[referral_order_product_serial_input!]')] public ?array $referralOrderProductSerials = null;` + `#[Assert\Valid]`.

### B2. Validation reserved (Input-level, GroupSequence-friendly)
Custom validator `ReservedProductItemConstraint` (hoặc `#[Assert\Callback]` gọi service) trên field serials:
- Với mỗi `product_item_id`: reject nếu tồn tại `referral_order_product_serial` thuộc order KHÁC mà order đó `status NOT IN (cancelled, client_rejected, ...)`.
- Loại trừ order hiện tại (`referralOrderId`/entity đang update).
- Chỉ áp khi `resell_type === sell_from_inventory`.

Repository: `ReferralOrderProductSerialRepository::findReservedProductItemIds(array $productItemIds, ?Uuid $excludeOrderId): array`
- JOIN `referral_order_product_serial` → `referral_order_product` → `referral_order`, filter `product_item_id IN (...)`, `referral_order.id != excludeOrderId`, `referral_order.status NOT IN (:releasedStatuses)`.
- Trả list product_item_id đang reserved → resolver/validator build violation.

Định nghĩa released statuses: dùng các status hủy/reject hiện có (`STATUS_CANCELLED`, `STATUS_CLIENT_REJECTED`, `STATUS_REJECTED`) — xác nhận danh sách với hằng trong `ReferralOrder`.

### B3. Persist (replace) — Service
Method mới `ReferralOrderService::syncProductSerials(ReferralOrder $order, ?array $serialInputs): void`:
1. Nếu `$serialInputs === null` → no-op (không đụng data hiện có). Nếu `[]` → xóa hết (replace bằng rỗng).
2. Xóa toàn bộ `referral_order_product_serial` hiện có của order (repo `deleteByOrder(Uuid $orderId)` hoặc qua association cascade).
3. Với mỗi input: resolve `ReferralOrderProduct` của order theo `product_id` (match product line), resolve `ResellerInventoryProductItem` theo `product_item_id`, tạo `ReferralOrderProductSerial{ referralOrderProduct, productItem, serialNumber ?? item.serialNumber }`, persist.
   - Nếu 1 product line có nhiều item: match theo thứ tự / gán lần lượt (nhiều serial cùng product_id → nhiều row cùng referralOrderProduct).
   - Reserved check lại ở service (defense-in-depth) trước khi persist, exclude order hiện tại.

Gọi `syncProductSerials`:
- **Create resolver**: sau khi `persist($order)` + `flush()` (cần product line có id) — chỗ sau line ~260, chỉ khi `sell_from_inventory`.
- **Update resolver**: sau khi rebuild products + flush, chỉ khi `sell_from_inventory`.
- **PreviewOrder resolver**: **KHÔNG persist** — chỉ chạy reserved-check (validate) + có thể build DTO tạm để trả về; không tạo `ReferralOrderProductSerial` trong DB.

### B4. sell_order_id — NGOÀI luồng assign
KHÔNG set khi assign serial. Set khi sell order chuyển **paid**:
- Hook: `OrderPaidMessageHandler` / `ReferralOrderPaidSubscriber` / `MarkPaid` — với order `sell_from_inventory` paid → set `reseller_inventory_product_item.sell_order_id = order.id` cho các product_item nằm trong `referral_order_product_serial` của order.
- Repo: `ResellerInventoryProductItemRepository::markSoldByOrder(Uuid $sellOrderId): void` (UPDATE ... SET sell_order_id WHERE id IN serials-of-order AND sell_order_id IS NULL).
- **Xác nhận scope**: task nói "khi sell order paid" — implement ở đây hay tách task sau? (Mặc định: implement luôn ở task này vì gắn liền reserved→sold. Cần confirm.)

---

## Files touched (tổng hợp)

**Part A**
- `src/Repository/Stock/ResellerInventoryProductItemRepository.php` (new methods)
- `src/Repository/Stock/AgentProductStockRepository.php` (`upsertSetBatch`)
- `src/Service/Stock/AgentProductStockService.php` (đổi logic + inject repo)
- `src/MessageHandler/ReferralOrder/AddAgentProductStockMessageHandler.php` (đổi tên gọi nếu cần)

**Part B**
- `src/GraphQL/ReferralOrder/ReferralOrderProductSerialInput.php` (new)
- `src/GraphQL/ReferralOrder/Mutation/Create/Input.php` (+field)
- `src/GraphQL/ReferralOrder/Mutation/Update/Input.php` (+field)
- `src/GraphQL/ReferralOrder/Mutation/PreviewOrder/Input.php` (+field)
- `src/GraphQL/ReferralOrder/Mutation/Create/Resolver.php` (gọi syncProductSerials)
- `src/GraphQL/ReferralOrder/Mutation/Update/Resolver.php` (gọi syncProductSerials)
- `src/GraphQL/ReferralOrder/Mutation/PreviewOrder/Resolver.php` (reserved-check only)
- `src/Service/ReferralOrder/ReferralOrderService.php` (`syncProductSerials`)
- `src/Repository/Stock/ReferralOrderProductSerialRepository.php` (`findReservedProductItemIds`, `deleteByOrder`)
- `src/Validator/Constraints/ReservedProductItemConstraint.php` (+ Validator) — hoặc Callback
- `src/Repository/Stock/ResellerInventoryProductItemRepository.php` (`markSoldByOrder`, nếu làm B4)
- Paid hook (`OrderPaidMessageHandler`/subscriber) cho B4.

**Docs**: sau khi xong chạy `scripts/extract_resolvers.py` nếu resolver signature đổi (ở đây chỉ thêm field input → catalog có thể cần regenerate).

---

## Edge cases / lưu ý
1. **Available vs reserved lệch**: item reserved ở order draft khác (sell_order_id vẫn null) vẫn bị count là available trong AgentProductStock. Reserved-check ở B2 là lớp chống bán trùng. (Theo quyết định Q&A — chấp nhận.)
2. **Số serial vs quantity**: cần validate số serial truyền cho 1 product_id ≤ quantity của product line đó? → **cần confirm** (chưa chốt trong Q&A). Đề xuất: reject nếu vượt quantity.
3. **product_item thuộc đúng reseller**: reserved-check nên kèm ràng buộc `product_item.reseller == order effective owner` + `product_item.product_id == input.product_id`. Reject nếu lệch.
4. **Concurrency reserved**: 2 order cùng add 1 item song song → race. Reserved-check + unique? Không có unique DB cho (product_item trong serial). Chấp nhận check-then-write trong transaction; nếu cần chặt hơn → unique partial index `referral_order_product_serial(product_item_id)` cho order active (khó vì phụ thuộc status). Ghi nhận, không làm ở task này trừ khi yêu cầu.
5. **Replace khi products đổi**: nếu update order bỏ 1 product line nhưng serial vẫn trỏ product_id đó → serial mồ côi. syncProductSerials phải chạy SAU khi rebuild products và chỉ chấp nhận serial có product line tương ứng.

## Verification
- `doctrine:schema:validate` (không mong đổi schema).
- Manual GraphQL: create purchase_to_inventory → mark paid → kiểm tra N rows `reseller_inventory_product_item` + `AgentProductStock.quantity == count`.
- create/update sell_from_inventory với serials → kiểm tra replace + reserved reject (add item đã ở order khác).
- preview → không tạo row DB.
- `vendor/bin/ecs check --fix` src/GraphQL.

## Đã confirm
- **(a)** B4 (set sell_order_id khi paid) → **làm trong task này**.
- **(b)** Edge #2 → **CÓ validate**: số serial của 1 product_id phải ≤ quantity của product line đó, reject nếu vượt.
- **(c)** Đổi tên `increaseFromOrder` → `importFromPurchaseOrder` → **OK**.
