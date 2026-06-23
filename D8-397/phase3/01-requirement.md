# Phase 3 — Unit price chọn thuần theo `user.type`

> ✅ **ĐÃ IMPLEMENT & VERIFY (2026-06-23)** — xem mục [Trạng thái triển khai](#trạng-thái-triển-khai) cuối file.

## Description

Đổi logic chọn `unitPrice` của sản phẩm về **thuần theo `user.type`**:

- `user.type === agent` → `unitPrice = priceForAgentAndMaster`
- `user.type === reseller` → `unitPrice = priceForReseller`
- Giữ nguyên check role `allow_for_*` (`allowForAgentAndMaster` / `allowForReseller`).

Hiện tại giá đang được chọn theo cờ "insider" (liên quan Fastboy) chứ không thuần theo type → cần bỏ override đó cho phần **chọn giá**.

## Logic hiện tại (trước khi đổi)

Biến quyết định `ReferralOrderService.php:290`:
```php
$useInsiderPrice = ($type === User::TYPE_AGENT && $isFastboy) || $isParentFastboyMasterAgent;
```
Chọn giá ở **2 chỗ** trong `applyCrmProductData`, cùng pattern:
- Avalara block `:312-315`
- Vòng chính `:376-379`
```php
if (allowForAgentAndMaster && $useInsiderPrice)   → priceForAgentAndMaster
elseif (allowForReseller && !$useInsiderPrice)     → priceForReseller
else                                                → 0 (+ throw khi submit)
```
(`sell_from_inventory` `:372` luôn lấy `priceForReseller` trước — cost reference.)

Song song, `CrmProductConstraintValidator::isUserAllowToMakeOrder` `:198-206` ép "effective type": nếu `isUserParentFastboyAgent` → coi như `TYPE_AGENT`, ngược lại `$user->getType()`; rồi match `allow` + `price` theo type đó.

**Hệ quả logic cũ (≠ mong muốn):**
- Agent **không** phải Fastboy + parent không Fastboy → `useInsiderPrice=false` → lấy nhầm `priceForReseller`.
- Reseller có parent là Fastboy master → `useInsiderPrice=true` → lấy nhầm `priceForAgentAndMaster`.

## Q&A (đã chốt)

1. **Bỏ hoàn toàn override insider khi chọn giá?** → ✅ **Bỏ hoàn toàn, thuần theo `user.type`** (bất kể Fastboy / parent-Fastboy).
2. **`user.type = null` dùng giá nào?** → **Không cho đặt giá**: rơi nhánh `else` → `unitPrice = 0` + throw khi submit (giống khi thiếu `allow`).
3. **Đồng bộ `CrmProductConstraintValidator`?** → ✅ **Có** — bỏ override `isUserParentFastboyAgent → TYPE_AGENT`, dùng thẳng `$user->getType()` để validation khớp giá thực dùng.
4. **Tax/shipping (đang key theo `isParentFastboyMasterAgent`: Fastboy master → miễn tax + không shipping) có đổi?** → **Giữ nguyên** — chỉ đổi phần chọn GIÁ, tax/shipping ngoài scope.

## Quy tắc giá mới (chốt)

```
if (user.isAgent() && allowForAgentAndMaster)      → priceForAgentAndMaster
elseif (user.isReseller() && allowForReseller)     → priceForReseller
else                                                → 0 (+ throw "Can't find price for ..." khi submit)
```
- `type = null` → không khớp agent/reseller → else → error khi submit.
- Giữ nhánh `sell_from_inventory` (luôn `priceForReseller`) và discount `purchase_to_inventory` (qty-based override).

## Scope cần update

1. **`ReferralOrderService::applyCrmProductData`**
   - 2 nhánh chọn giá (Avalara `:312-315` + vòng chính `:376-379`): thay điều kiện `$useInsiderPrice` → `$user->isAgent()` / `$user->isReseller()`.
   - `$useInsiderPrice` (`:290`) thành dead var → xóa; kiểm tra & dọn `$isFastboy` (`:252`) nếu không còn chỗ dùng.
   - **Giữ nguyên** nhánh `sell_from_inventory` (`:372`), discount `purchase_to_inventory` (`:390`), và `$isParentFastboyMasterAgent` cho tax/shipping (`:437`, `:441`, `:491`).

2. **`CrmProductConstraintValidator::isUserAllowToMakeOrder`** (`:198-206`)
   - Bỏ override `isUserParentFastboyAgent → TYPE_AGENT`; dùng `$userType = $user->getType()`.
   - `match` theo `$userType` cho `allow` + `price` (giữ nguyên). `type=null` → `default` → không allowed → violation (khớp rule error).
   - **KHÔNG đụng** block `salePrice` `:116-138` (gate theo `isParentFastboyAgent`) — thuộc concern sale_price (requirement #5), tách riêng.

## Impact (lưu ý khi review/QA)

- Reseller có parent là Fastboy master: trước được giá agent → **giờ giá reseller**.
- Agent non-Fastboy: trước bị tính giá reseller → **giờ đúng giá agent**.
- `user.type = null`: trước rơi nhánh reseller (có giá) → **giờ không đặt được giá**, submit sẽ lỗi.

## Notes

- `applyCrmProductData` dùng chung cho create / update / preview → sửa 1 chỗ cover hết.
- Không cần migration / field mới.
- Verify: smoke test preview/create với token agent (→ priceForAgentAndMaster) và reseller (→ priceForReseller); product chỉ `allow_for_reseller` mà user là agent → error; ngược lại tương tự.

## Trạng thái triển khai

✅ **DONE (2026-06-23)**

**Đã sửa:**
- `ReferralOrderService::applyCrmProductData`:
  - Bỏ `$type` + `$useInsiderPrice`; thêm `$isAgentPrice = $user->isAgent()`, `$isResellerPrice = $user->isReseller()`.
  - 2 nhánh chọn giá (Avalara closure + vòng chính): `allowForAgentAndMaster && $isAgentPrice` → agent price; `allowForReseller && $isResellerPrice` → reseller price; else → 0 + throw khi submit.
  - Giữ `$isFastboy` (còn dùng ở commission `:561-562`), `$isParentFastboyMasterAgent` (tax/shipping), nhánh `sell_from_inventory`, discount `purchase_to_inventory`.
- `CrmProductConstraintValidator::isUserAllowToMakeOrder`: bỏ override `isUserParentFastboyAgent → TYPE_AGENT`, dùng `$userType = $user->getType()`.

**Verify (smoke test preview qua `/graphql`):**
- Product `888368f0` (agent=150, reseller=752): agent (phu_vo) → **150**; reseller (lenguyen) → **752**. ✅
- Product chỉ `allow_for_reseller` + agent xem → `unit_price=0` (allow gate giữ; submit sẽ throw). ✅
- Không còn tham chiếu `useInsiderPrice`/`$type`; lint sạch.
