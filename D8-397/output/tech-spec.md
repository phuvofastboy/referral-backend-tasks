# Tech Spec — Agent Purchase Products & Nhập kho riêng của Agent

**Task:** D8-397
**Liên quan:** [01-requirement.md](../01-requirement.md) · [phase1/01-requirement.md](../phase1/01-requirement.md) · [docs/domains/referral-order.md](../../../docs/domains/referral-order.md) · [docs/business/referral-order.md](../../../docs/business/referral-order.md)

> **Cập nhật:** dùng field `resell_type` (thay `order_type`/`agent_purchase`). **Phase 1 đã hoàn thành** (xem mục 2). Phase 2 = kho agent + side-effect khi paid. **Create flow KHÔNG strip-down** — đơn `purchase_to_inventory` tạo y hệt đơn thường, chỉ khác khi paid (bỏ commission + trial, cộng kho).

---

## 1. Mục tiêu

Cho phép **Agent** tự mua sản phẩm để **nhập về kho riêng** của họ, thay vì bán cho khách. Khi đơn `purchase_to_inventory` được thanh toán (`paid`), số lượng sản phẩm được cộng vào tồn kho riêng của agent ở bảng mới `agent_product_stock` (BE là source of truth).

### `resell_type` — phân loại đơn (enum string)

| Giá trị | Ý nghĩa | FE truyền được? |
|---|---|---|
| `sell_via_crm` | Mặc định — đơn bán cho khách qua CRM (đơn thường) | ✅ |
| `purchase_to_inventory` | Agent mua hàng nhập kho riêng (**feature này**) | ✅ |
| `sell_from_inventory` | Bán từ kho agent — **chưa hỗ trợ**, chỉ khai báo sẵn | ❌ (chưa) |

`null` (đơn cũ) ≡ `sell_via_crm` qua `ReferralOrder::getEffectiveResellType()`.

### Khác biệt `purchase_to_inventory` so với đơn thường (`sell_via_crm`)

**Quan trọng:** khi **TẠO đơn**, `purchase_to_inventory` xử lý **y hệt đơn thường** — KHÔNG strip-down. Khác biệt **chỉ ở thời điểm `paid`**.

| Khía cạnh | Đơn thường | `purchase_to_inventory` |
|---|---|---|
| Client info / company / shipping address | Bắt buộc (XOR) | **Như đơn thường** (FE vẫn gửi) |
| E-sign document (quote) | Có | **Như đơn thường** |
| Trừ stock CRM trung tâm (`Crm/ChangeProductStock`) | Có | **Như đơn thường** (vẫn trừ) |
| Tax / shipping fee | Có | **Như đơn thường** (vẫn tính) |
| Pay Now | Tùy | **Như đơn thường** (không ép) |
| **Commission** (`OrderCommission` + `ChangeUserAmount`) khi paid | Có | **BỎ** |
| **Trial / Referral** khi paid | Tùy chọn | **BỎ** |
| PDF / email / pay slip / CRM-noti khi paid | Có | **Giữ** |
| Side-effect MỚI khi paid | — | **Cộng `agent_product_stock`** |

> Mô hình này khớp curl `CreateOrder` từ FE dev (vẫn truyền `company` + `shipping_address`). **Không còn tension** — FE không phải đổi payload create.

---

## 2. Trạng thái triển khai

### ✅ Phase 1 — ĐÃ XONG (commit `62737109` + follow-up)

- `ReferralOrder`: consts `RESELL_TYPE_*`, `RESELL_TYPES`, `RESELL_TYPES_SUPPORTED` (chỉ `sell_via_crm` + `purchase_to_inventory`), field `resellType` (varchar 32, nullable, **indexed** `idx_referral_order_resell_type`), helper `getEffectiveResellType()`.
- Migration `Version20260617072022` — `ADD resell_type` + index.
- Create `Input`: field `resell_type` + `Assert\Choice(RESELL_TYPES_SUPPORTED)` (FE không truyền được `sell_from_inventory`).
- Create `Resolver` + `ReferralOrderService::create()`: nhận `resellType`, default `sell_via_crm` nếu null.
- `ReferralOrderEntityType`: expose `resell_type`.
- Hasura remote schema permission SDL (3 role) + metadata applied.
- **CRM sync**: `referralOrderPaidNoti()` + `Crm/UpdateReferralOrder.graphql` gửi `resellType` (required) sang CRM khi paid; chain `OrderPaidMessage`/`OrderPaidMessageHandler`/`ReferralOrderPaidSubscriber` truyền `resell_type` (fallback `sell_via_crm`).

→ Hiện tại đơn `purchase_to_inventory` được tạo & lưu, đi luồng **bình thường** (đúng thiết kế — chỉ còn thiếu side-effect khi paid).

### ☐ Phase 2 — CÒN LẠI (tài liệu này)

Bảng `agent_product_stock` + guard `stock_imported_at` + cộng kho khi paid + skip commission/trial khi paid. **Create flow KHÔNG đổi.**

---

## 3. Database changes (phase 2)

### 3.1. Bảng mới `agent_product_stock`

| Cột | Kiểu | Ràng buộc | Ghi chú |
|---|---|---|---|
| `id` | uuid | PK | UUID v7 (`UuidV7Generator`) |
| `agent_id` | uuid | FK → `user(id)` `ON DELETE CASCADE`, NOT NULL | ManyToOne `User` (= `referral_order.created_by`) |
| `product_id` | varchar | NOT NULL | CRM product id (string), **không** phải FK DB |
| `quantity` | integer | NOT NULL, default 0 | Tồn kho cộng dồn |
| `created_at` | timestamp(0) | nullable | Timestampable on create |
| `updated_at` | timestamp(0) | nullable | Timestampable on update |

**Constraint:** `UNIQUE (agent_id, product_id)` (tên `uniq_agent_product_stock_agent_product`) — bắt buộc cho upsert `ON CONFLICT`.

### 3.2. Cột mới trên `referral_order`

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `stock_imported_at` | timestamp(0) nullable | **Guard idempotency** — set sau khi cộng kho; re-process là no-op |

> `resell_type` đã thêm ở phase 1, không lặp lại.

### 3.3. Migration

- Generate bằng `doctrine:migrations:diff` rồi **dọn drift** (bỏ `CREATE SCHEMA hdb_catalog` rác trong `down()` như `Version20260617072022`).
- `CREATE TABLE agent_product_stock` + unique; `ALTER TABLE referral_order ADD stock_imported_at`.
- `ADD COLUMN` nullable + bảng mới rỗng → zero-downtime an toàn.
- Review qua skill `db-migration-safety` / agent `migration-reviewer`.

### 3.4. Hasura metadata

- **Track** `agent_product_stock` (FE query tồn kho qua Hasura nếu cần) + set permission cho role phù hợp.
- Remote schema permission SDL: **không đổi** (agent_product_stock là bảng Hasura-tracked, không phải remote schema Symfony). Nếu expose qua mutation/query Symfony mới thì mới phải thêm SDL.
- Event-trigger `ReferralOrderPaid`: payload `data.new` cần có `resell_type`, `stock_imported_at`, `created_by_id` (mặc định Hasura gửi toàn bộ cột → OK).

---

## 4. Code changes (phase 2)

### 4.1. Entity

**`app/src/Entity/Stock/AgentProductStock.php`** (mới)
- `#[ORM\Entity(repositoryClass: AgentProductStockRepository::class)]`, `#[ORM\Table(name: 'agent_product_stock')]`, `#[ORM\UniqueConstraint(name: 'uniq_agent_product_stock_agent_product', columns: ['agent_id', 'product_id'])]`.
- Fields: `id` (UUID v7), `agent` (ManyToOne `User`, JoinColumn `onDelete: 'CASCADE'`, nullable false), `productId` (string), `quantity` (int), `createdAt`/`updatedAt` (Gedmo Timestampable).

**`app/src/Entity/ReferralOrder/ReferralOrder.php`** (sửa thêm)
```php
#[ORM\Column(type: 'datetime', nullable: true)]
private ?DateTimeInterface $stockImportedAt = null;
// + getStockImportedAt/setStockImportedAt
// + helper: public function isPurchaseToInventory(): bool
//           { return $this->getEffectiveResellType() === self::RESELL_TYPE_PURCHASE_TO_INVENTORY; }
```

### 4.2. Repository

**`app/src/Repository/Stock/AgentProductStockRepository.php`** (mới)
- `upsertIncrement(Uuid $agentId, string $productId, int $quantity): void` — raw SQL `INSERT ... ON CONFLICT (agent_id, product_id) DO UPDATE SET quantity = agent_product_stock.quantity + EXCLUDED.quantity, updated_at = now()`. Atomic, tránh race condition giữa 2 message song song.

### 4.3. GraphQL — Create mutation — **KHÔNG ĐỔI**

Create flow xử lý `purchase_to_inventory` y hệt đơn thường (client/company, shipping, e-sign, CRM stock, tax/shipping). `resell_type` đã được lưu ở phase 1. **Không cần sửa Input/Resolver/Service ở create.**

- **(Open)** Permission: có giới hạn chỉ `isAgent()` mới tạo được `purchase_to_inventory` không? Nếu có → thêm guard nhỏ ở Resolver. Xem mục 8.

### 4.4. Service `ReferralOrderService` — **KHÔNG ĐỔI ở create**

Commission snapshot (`creatorCommissionAmount`) vẫn tính ở create như thường — vô hại vì side-effect commission bị bỏ ở paid (mục 4.6).

### 4.5. Async message — cộng kho khi paid

**`app/src/Message/ReferralOrder/AddAgentProductStockMessage.php`** (mới) — `implements AsyncMailMessageInterface` → route `async_common`; payload `?string $orderId`.

**`app/src/MessageHandler/ReferralOrder/AddAgentProductStockMessageHandler.php`** (mới):
1. Load `ReferralOrder` theo `orderId`; null → return.
2. Guard: `if (!$order->isPurchaseToInventory()) return;`
3. **Guard idempotency**: `if ($order->getStockImportedAt() !== null) return;`
4. Guard status: `if ($order->getStatus() !== STATUS_PAID) return;`
5. Mỗi `ReferralOrderProduct` (bỏ `isShippingProduct`): `repo->upsertIncrement($order->getCreatedBy()->getId(), productId, quantity)`.
6. `$order->setStockImportedAt(now())` + flush.
- Bước 5+6 atomic trong 1 transaction (handler trên transactional middleware).

### 4.6. EventSubscriber `ReferralOrderPaidSubscriber` (sửa)

Trong nhánh `newStatus === STATUS_PAID && oldStatus !== STATUS_PAID`:
```php
if (($newData['resell_type'] ?? null) === ReferralOrder::RESELL_TYPE_PURCHASE_TO_INVENTORY) {
    $this->bus->dispatch(new AddAgentProductStockMessage($newData['id']));
    // BỎ commission: không dispatch OrderPaidMessage commission-amount + ChangeUserAmountMessage
    // GIỮ: referralOrderPaidNoti (CRM sync resellType) + PDF + email biên nhận
}
```
- **Skip commission**: không dispatch `ChangeUserAmountMessage`; trong `OrderPaidMessageHandler` không tạo `OrderCommission` cho đơn này.
- **Skip trial**: vì create flow không strip nên agent vẫn có thể set `createReferral`/`createTrial` → cần **guard tường minh**: bỏ trial flow (`CreateTrialFromReferralOrderMessage` / `ClientConfirmOrderTrialSubscriber`) khi `resell_type === purchase_to_inventory`.
- **Giữ** `referralOrderPaidNoti` (gửi `resellType` sang CRM — phase 1), PDF, email/pay slip, và **trừ CRM stock vẫn diễn ra ở create/submit** (không liên quan subscriber).

> **⚠ Lưu ý phối hợp với CRM sync (phase 1):** `OrderPaidMessageHandler` hiện luôn gọi `referralOrderPaidNoti(..., resellType)`. Phải đảm bảo nhánh skip-commission **không** vô tình bỏ luôn `referralOrderPaidNoti`. Tách rõ: CRM-noti + PDF + email = giữ; commission + trial = skip.

> **Hai lớp idempotency:** (1) subscriber guard `oldStatus === paid` chặn duplicate event; (2) handler guard `stock_imported_at !== null` + upsert atomic chặn double-add khi message retry.

---

## 5. Luồng end-to-end (`purchase_to_inventory`)

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Frontend)
    participant API as referral-backend
    participant CRM as Hasura/CRM
    participant PG as Payment Gateway
    participant H as Hasura (event trigger)
    participant MQ as RabbitMQ (async_common)
    participant HND as AddAgentProductStockMessageHandler
    participant DB as PostgreSQL

    A->>API: referral_order_create_mutation(resell_type=purchase_to_inventory, company, shipping, products)
    API->>API: tạo đơn Y HỆT đơn thường (client/shipping/document/CRM stock/tax)
    API->>CRM: Crm/GetProductTax + ChangeProductStock (trừ như thường)
    API->>DB: persist ReferralOrder(resell_type=purchase_to_inventory)
    API-->>A: ReferralOrder { id, total, resell_type }
    A->>CRM: Thanh toán (thẻ agent)
    CRM->>PG: Charge
    PG-->>CRM: Settled
    CRM->>API: referral_order_update_status_by_transaction_mutation → paid
    API->>DB: status = paid
    H->>MQ: ReferralOrderPaid (data.new.resell_type=purchase_to_inventory)
    Note over MQ: skip commission + trial;<br/>giữ CRM-noti + PDF + email/pay slip
    MQ->>HND: AddAgentProductStockMessage(orderId)
    HND->>HND: guard isPurchaseToInventory + stock_imported_at==null + status==paid
    loop mỗi ReferralOrderProduct (bỏ shipping)
        HND->>DB: upsert agent_product_stock (agent_id, product_id) quantity += qty
    end
    HND->>DB: set referral_order.stock_imported_at = now()
    API->>CRM: referralOrderPaidNoti(resellType) [vẫn chạy]
```

---

## 6. Checklist triển khai (phase 2)

- [ ] Entity `AgentProductStock` + Repository `upsertIncrement`.
- [ ] `ReferralOrder`: thêm `stockImportedAt` + helper `isPurchaseToInventory()`.
- [ ] Migration: create table + unique + `stock_imported_at` (diff + dọn drift, review `migration-reviewer`).
- [ ] Hasura: track `agent_product_stock` + permission.
- [ ] (Open) Permission `isAgent()` cho `purchase_to_inventory` — nếu business yêu cầu.
- [ ] `AddAgentProductStockMessage` + handler (upsert + guard idempotency).
- [ ] `ReferralOrderPaidSubscriber`: dispatch stock message + skip commission + skip trial, **giữ** CRM-noti/PDF/email.
- [ ] Guard skip trial: `ClientConfirmOrderTrialSubscriber`/`CreateTrialFromReferralOrderMessage` bỏ qua `purchase_to_inventory`.
- [ ] Regenerate catalog: `extract_async_messages.py` (message mới) + `extract_erd.py` (entity/field mới) → `check-generated-docs.sh` pass.
- [ ] Cập nhật docs `docs/domains/referral-order.md` + `docs/business/referral-order.md`.

> **Create flow KHÔNG đổi** — không sửa Input/Resolver/Service ở create (trừ permission nếu cần).

---

## 7. Rủi ro & Edge cases

1. **Idempotency double-add** (cao nhất) — 2 lớp (mục 4.6). Test: duplicate `ReferralOrderPaid` + retry message → quantity cộng đúng 1 lần.
2. **Race condition** 2 message song song cùng `(agent, product)` → `ON CONFLICT` atomic xử lý.
3. **Refund (`paid → cancelled`)** — repo **không** auto-hoàn stock CRM. Đơn purchase_to_inventory bị cancel sau paid **chưa** có cơ chế trừ ngược `agent_product_stock`. → **Ngoài scope**, TODO nếu business cần.
4. **Shipping product** trong line items — handler bỏ qua `isShippingProduct=true` khi cộng kho.
5. **Vô tình bỏ CRM-noti** khi skip commission (mục 4.6) — phải tách rõ CRM-noti khỏi commission.
6. **Skip trial khi create flow bình thường** — agent vẫn có thể set `createTrial`; phải guard skip ở paid (mục 4.6), nếu không trial bị tạo nhầm.
7. **`sell_from_inventory`** — chưa hỗ trợ; `RESELL_TYPES_SUPPORTED` đã chặn FE truyền. Không xử lý logic gì ở phase này.

---

## 8. Câu hỏi mở (confirm với business / FE / CRM)

- **Permission**: có giới hạn chỉ `isAgent()` mới tạo `purchase_to_inventory` không? (create flow hiện không check).
- **Refund**: có trừ lại `agent_product_stock` khi `paid → cancelled`? (đề xuất out-of-scope).
- **CRM dùng resellType làm gì**: CRM đã nhận `resellType` (phase 1) — xác nhận CRM không tự quản inventory agent (BE là source of truth).
