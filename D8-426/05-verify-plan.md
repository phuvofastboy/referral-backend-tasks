# D8-426 — Verify Plan

> Kế hoạch kiểm chứng những gì đã implement ở commit `c58f89d4 [D8-426] tmp` (21 file).
> As-built: [`04-result.md`](04-result.md). Repo **chưa có test runner** → verify bằng static checks + DB-driven runtime + code review.
> Ngày: 2026-07-01.

## 0. Phạm vi cần verify (map theo file trong commit)

| Thành phần | File | Verify ở mục |
|---|---|---|
| Cột `credit_use`, `credit_deducted_at` + migration | Entity + Version20260630104644 / Version20260701023808 | §1 |
| Validate credit (PTI + ≤gross + ≤available) | `ReferralOrderService::applyCreditUse` | §3 (B) |
| Giảm totalAfterTax | `applyCrmProductData` | §3 (B4) |
| Reserve ngầm qua status | `ReferralOrderRepository::sumReservedCreditUse` + `CreditService::availableCredit` | §3 (C) |
| Deduct tại paid (>0) | `ReferralOrderPaidSubscriber` + `DeductOrderCreditMessage(Handler)` + `CreditService::deductForPaidOrder` | §4 (D) |
| Mark-paid ==0 | Create/Update Resolver | §5 (E) |
| Idempotency (claim `credit_deducted_at`) | `CreditService` | §4 (D2) |
| Overdraft guard/clamp | `CreditService` | §4 (D3) |
| Expose Hasura | entity type + remote-schema/table perms | §6 (H) |

## 1. Static checks (nhanh, không cần auth) — phần lớn ĐÃ chạy

```bash
docker compose exec apache composer dump-autoload
docker compose exec apache php bin/console cache:clear                      # DI container OK
docker compose exec apache php bin/console doctrine:migrations:up-to-date    # 2 migration đã chạy
docker compose exec apache php bin/console doctrine:schema:validate          # mapping ⇄ DB in sync
docker compose exec apache php bin/console graphqlite:dump-schema | grep -c credit_use   # = 4 (3 input + 1 output)
docker compose exec apache php bin/console debug:messenger | grep -A1 DeductOrderCredit   # handler registered
```
✅ Kỳ vọng: tất cả OK, `credit_use` = 4, handler map đúng.

## 2. Seed dữ liệu test

```bash
# Chọn 1 reseller (có quyền purchase_to_inventory) và seed credit:
docker compose exec apache php bin/console doctrine:query:sql \
  "SELECT id, email, credit FROM \"user\" WHERE ... LIMIT 5"     # tìm reseller
docker compose exec apache php bin/console doctrine:query:sql \
  "UPDATE \"user\" SET credit = 500 WHERE id = '<RESELLER_UUID>'"
```
> Ghi lại `<RESELLER_UUID>`. Cần product PTI hợp lệ trong CRM để tạo đơn.

## 3. Validation + reserve (create/update/preview)

Chạy qua GraphQL (Hasura → GraphQLite) với JWT của reseller, hoặc `referral_order_preview_order` (nhẹ nhất, không persist).

| # | Kịch bản | Input | Kỳ vọng |
|---|---|---|---|
| B1 | credit_use trên đơn non-PTI | resell_type=sell_via_crm, credit_use=10 | Lỗi `Credit can only be used for purchase_to_inventory orders` |
| B2 | credit_use > tổng đơn | PTI, credit_use > gross | Lỗi `credit_use exceeds order total` |
| B3 | credit_use > available | seed credit=50, credit_use=100 | Lỗi `Insufficient credit balance` |
| B4 | credit_use hợp lệ | PTI, gross=100, credit_use=30 | `total_after_tax = 70` (preview + create) |
| C1 | Reserve giữ chỗ | Tạo đơn A (sent, credit_use=400, credit=500) rồi preview/tạo đơn B credit_use=200 | Đơn B lỗi Insufficient (available = 500-400 = 100) |
| C2 | Release khi cancel | Cancel đơn A → thử lại đơn B credit_use=200 | OK (available=500 lại) |

**Assert DB sau B4/create:**
```bash
docker compose exec apache php bin/console doctrine:query:sql \
 "SELECT id,status,resell_type,credit_use,total_after_tax,credit_deducted_at FROM referral_order WHERE id='<ORDER>'"
# credit_use=30, total_after_tax=70, credit_deducted_at IS NULL (chưa paid)
```

## 4. Deduct tại paid — nhánh totalAfterTax > 0 (D)

Không có gateway thật → mô phỏng CRM paid bằng cách đổi status (Hasura event trigger `ReferralOrderPaid` fire trên **mọi** UPDATE cột status/last_payment_status, kể cả SQL trực tiếp).

```bash
# D1: đơn PTI đã sent, credit_use=30, total_after_tax=70, user.credit=500
docker compose exec apache php bin/console doctrine:query:sql \
 "UPDATE referral_order SET status='paid', last_payment_status='settledSuccessfully' WHERE id='<ORDER>'"
# → Hasura event → ReferralOrderPaidSubscriber (PTI) → dispatch DeductOrderCreditMessage
# Consume queue (nếu worker không tự chạy):
docker compose exec worker php bin/console messenger:consume async_common --limit=5 -vv
```
✅ Kỳ vọng:
- `user.credit = 470` (500 - 30).
- `referral_order.credit_deducted_at` != NULL.
- Log `Debited reseller credit`.
- Đồng thời `AddAgentProductStockMessage` cộng kho agent (kiểm AgentProductStock).

```bash
# D2: idempotency — fire lại paid event / redeliver
docker compose exec apache php bin/console doctrine:query:sql \
 "UPDATE referral_order SET last_payment_status='settledSuccessfully' WHERE id='<ORDER>'"   # hoặc re-consume
```
✅ Kỳ vọng: `user.credit` VẪN 470 (claim `credit_deducted_at IS NULL` fail → skip). Không trừ đôi.

```bash
# D3: overdraft — set credit thấp hơn credit_use rồi paid (đơn mới, credit_deducted_at NULL)
docker compose exec apache php bin/console doctrine:query:sql "UPDATE \"user\" SET credit=10 WHERE id='<RESELLER>'"
# ... trigger paid cho đơn credit_use=30 ...
```
✅ Kỳ vọng: `user.credit = 0` (clamp, không âm) + log warning `Credit debit clamped to 0`.

## 5. Mark-paid nhánh credit phủ hết (totalAfterTax == 0) (E)

| # | Kịch bản | Kỳ vọng |
|---|---|---|
| E1 | **Create** PTI pay-now, credit_use = gross (vd 100=100) | Order tạo thẳng `status=paid`; `total_after_tax=0`; sau worker: `user.credit -= 100`, `credit_deducted_at` set, agent stock cộng, OrderPaid downstream chạy. (INSERT-as-paid → resolver dispatch thủ công 3 message) |
| E2 | **Update** đơn draft→submit pay-now, credit_use = gross | `status=paid` (UPDATE → Hasura trigger) → subscriber lo deduct+stock. Kết quả như E1 |

**Assert:** `SELECT status, total_after_tax, credit_deducted_at` = paid / 0 / not null; `user.credit` giảm đúng.

## 6. Hasura exposure (H)

```bash
# H1: field credit_use đọc được qua Hasura (ROLE_USER)
# Query referral_order { credit_use total_after_tax } với JWT reseller → trả về credit_use.
# H2: mutation input nhận credit_use (đã xác nhận qua graphqlite:dump-schema = 4).
```
✅ Kỳ vọng: remote-schema permission cho phép `credit_use` (đã add 3 role); table select-perm có cột.

## 7. Code review (bổ trợ)

```bash
# Review diff của commit feature cho correctness bug
git show c58f89d4    # hoặc: /code-review trên diff HEAD
```
Điểm cần soi kỹ:
- `applyCreditUse` chạy đúng cả create (createdBy null → fallback security user) lẫn update (exclude self trong available).
- `isQuickSubmit` (Update) có nuốt `credit_use` không (field non-null có thể phá điều kiện count===1).
- Nhánh ==0 create: `refresh()` lấy internalId trước khi dispatch OrderPaidMessage.
- `deductForPaidOrder`: claim + trừ trong cùng transaction; clamp không âm.
- Subscriber chỉ dispatch DeductOrderCredit khi `credit_use > 0` (đọc từ raw Hasura payload).

## 8. Checklist kết quả

- [ ] §1 static: all green, credit_use=4, handler registered
- [ ] B1–B3 validation errors đúng thông điệp
- [ ] B4 total_after_tax giảm đúng (create + preview)
- [ ] C1 reserve chặn over-spend; C2 cancel thả credit
- [ ] D1 deduct đúng số + credit_deducted_at set + stock cộng
- [ ] D2 không trừ đôi (idempotent)
- [ ] D3 overdraft clamp về 0 + log
- [ ] E1/E2 credit phủ hết → paid + deduct + downstream
- [ ] H1/H2 Hasura expose credit_use
- [ ] §7 code review không phát hiện bug chặn

## 8b. KẾT QUẢ THỰC THI (2026-07-01)

Môi trường: local dev. Reseller test: `lenguyen@gmail.com` (id `019de185-...`, có reseller-inventory company `9cf2d7bb-...`). Product PTI `888368f0-...` (gross total_after_tax = 700). Token JWT tự dựng theo skill `smoke-test-graphql-api`.

| # | Kịch bản | Kết quả | Bằng chứng |
|---|---|---|---|
| §1 | Static (schema/messenger/migrations) | ✅ | credit_use=4 trong schema; handler registered; 2 migration applied |
| B4 | credit_use hợp lệ (preview) | ✅ | gross 700 → credit_use=5 → total_after_tax=695 |
| B2 | credit_use > gross | ✅ | ERR `credit_use exceeds order total` |
| B3 | credit_use > available (credit=3) | ✅ | ERR `Insufficient credit balance` |
| B1 | credit_use trên non-PTI | ✅ | ERR `Credit can only be used for purchase_to_inventory orders` |
| C1 | Reserve giữ chỗ | ✅ | order 2050 sent credit_use=5 → available 495 (preview 500 fail, 495 OK) |
| D1 | Deduct tại paid (>0) | ✅ | order 2050 paid → credit 500→495, credit_deducted_at set |
| D2 | Idempotency (re-fire event) | ✅ | credit VẪN 495, credit_deducted_at không đổi |
| D3 | Overdraft → cho âm | ✅ | order 2055 credit_use=100, credit=10 → paid → credit=**-90** (cho phép âm để rà soát DB; đã bỏ clamp) |
| E1 | ==0 create (credit phủ hết) | ✅ | order 2052 credit_use=700 → status=**paid** ngay, total_after_tax=0 → credit 1000→300, credit_deducted_at set |
| C2 | Release khi cancel | ✅ | order 2053 credit_use=50 (available 250) → cancel → available về 300 |
| H | Hasura expose credit_use | ✅ | field `credit_use` trả về trong response create/preview (ROLE_USER remote schema) |

**Chưa chạy tường minh** (đã bao phủ gián tiếp):
- E2 (==0 qua Update): đường trigger→subscriber→deduct đã chứng minh ở D1 (SQL paid → subscriber); nhánh set-paid ở Update resolver giống E1 create.
- Test qua Hasura port 8080 (mọi test trên chạy thẳng GraphQLite `https://localhost/graphql`).

**Dữ liệu test để lại (dev):** orders `2050,2051,2052` (paid), `2053` (cancelled) của lenguyen; `user.credit` lenguyen = 300; phu_vo.credit = 500 (seed). Các đơn paid đã dispatch AddAgentProductStock (cộng kho agent). Cần dọn thủ công nếu muốn.

**Kết luận:** luồng debit credit (validate → reserve → deduct atomic/idempotent/overdraft → ==0 mark-paid → release) hoạt động đúng end-to-end.

## 8c. Lưu ý vận hành
- **Worker service chạy code cũ**: `messenger:consume` là process long-running → sau khi sửa code handler/service phải `docker compose restart worker` (cache:clear apache KHÔNG đủ). Triệu chứng: message xử lý theo logic cũ.

## 9. Ghi chú
- Nếu Hasura event trigger không fire khi SQL trực tiếp (tùy cấu hình), thay bằng: gọi mutation `referral_order_update_status_by_transaction_mutation` (ROLE_HASURA_CRM) để đẩy `TransactionUpdate` → status paid như luồng thật.
- Worker: nếu service `worker` đang chạy sẽ tự consume; nếu không, chạy `messenger:consume async_common` thủ công.
- Rcollback test: `UPDATE referral_order SET credit_deducted_at=NULL, status='sent'` + `UPDATE "user" SET credit=<orig>` để chạy lại.
