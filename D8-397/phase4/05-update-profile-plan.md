# Phase 4 — Plan: thêm `owner_phone` cho User profile

**Requirement:** [04-update-profile-requirement.md](04-update-profile-requirement.md)
**Mục tiêu:** profile address của User có thêm `owner_phone` để đủ bộ giống company address (`Company` đã có cả `business_phone` + `owner_phone`).

> Toàn bộ mirror cách `business_phone` đang làm trên User.

## Quyết định (Q&A đã chốt)
1. **Chỉ BE + Hasura** — lưu DB + expose qua Hasura user table, **không** sync CRM/WHMCS.
2. **Mirror `business_phone`** — cùng gate (`reseller || !isFastboyUser`), cho clear bằng `""` → null, `AssertPhoneNumber`, optional.
3. **Expose đọc cho `ROLE_USER` + `ROLE_HASURA_CRM`** (giống business_phone).
4. **Độc lập** — không auto-link / auto-fill inventory company (phase 4). Chỉ là field profile.

## Hiện trạng (tham chiếu)
- `User` có `businessPhone` (`phone_number`, nullable) — `User.php:118-119` — nhưng **thiếu `ownerPhone`**.
- `user_update` mutation: `UpdateMutation/Input.php` (field `business_phone` + address), `Resolver.php` (parse phone, xử lý `""`→null, gọi `patchUser`), `Output.php` chỉ trả `id` → FE đọc profile qua **Hasura `user` table**.
- `UserService::patchUser` set field trong block gate `if ($user->getType() === RESELLER || !isFastboyUser(...))`.
- `AdminUpdateMutation` (CRM) chỉ set flags (merchant/status/commission/canMakeOrder) → **không liên quan**.
- Hasura `public_user.yaml`: `business_phone` có trong columns của ROLE_HASURA_CRM (`:89`) + ROLE_USER (`:118`).
- SDL `user_update_mutation_input` (role_roleuser): có `business_phone` (`:482+`).

---

## Scope cần làm

### 1. Entity `User`
- Thêm field mirror `businessPhone`:
  ```php
  #[ORM\Column(type: 'phone_number', nullable: true)]
  private ?PhoneNumber $ownerPhone = null;
  // + getOwnerPhone(): ?PhoneNumber / setOwnerPhone(?PhoneNumber): void
  ```

### 2. Migration
- `doctrine:migrations:diff` → `ALTER TABLE "user" ADD owner_phone VARCHAR(35) DEFAULT NULL` (phone_number map sang varchar; theo cách business_phone đang lưu). Dọn drift `hdb_catalog` trong `down()`. Review `migration-reviewer`.
- `ADD COLUMN` nullable → zero-downtime an toàn.

### 3. `UpdateMutation/Input.php`
- Thêm field sau `business_phone`:
  ```php
  #[Graphql\Field(name: 'owner_phone', inputType: 'String')]
  #[AssertPhoneNumber]
  public ?string $ownerPhone = null;
  ```

### 4. `UpdateMutation/Resolver.php`
- Parse `owner_phone` y hệt `business_phone`:
  ```php
  $ownerPhone = in_array($inputObj->ownerPhone, [null, '', '0'], true) ? null : PhoneNumberUtil::getInstance()->parse($inputObj->ownerPhone);
  if ($inputObj->ownerPhone === "") {
      $currentUser->setOwnerPhone(null);
  }
  ```
- Truyền `$ownerPhone` vào `patchUser(...)` (thêm tham số).

### 5. `UserService::patchUser`
- Thêm param `?PhoneNumber $ownerPhone = null` (cuối, hoặc cạnh `$businessPhone`).
- Set trong **cùng block gate** với `businessPhone`:
  ```php
  if (!is_null($ownerPhone)) {
      $user->setOwnerPhone($ownerPhone);
  }
  ```

### 6. Hasura `public_user.yaml`
- Thêm cột `owner_phone` vào `columns` của **ROLE_USER** và **ROLE_HASURA_CRM** (cạnh `business_phone`).
- `hasura:metadata:apply`. *(File có thể bị hook `protect-sensitive` chặn → mở quyền/sửa tay.)*

### 7. SDL `role_roleuser.yaml`
- Thêm `owner_phone: String` vào `input user_update_mutation_input` (cạnh `business_phone`).
- `hasura:metadata:apply`.

---

## Ảnh hưởng / KHÔNG đụng
- `AdminUpdateMutation` (CRM update user): không liên quan (chỉ flags) → **không sửa**.
- `UpdateMutation/Output.php`: vẫn chỉ trả `id` → **không đổi** (FE đọc owner_phone qua Hasura user table).
- **Không** CRM/WHMCS sync.
- **Không** liên kết inventory company (phase 4) — độc lập.
- `phone_number` Doctrine type: lưu DB dạng varchar (như business_phone), Hasura expose raw string.

## Edge cases
- `owner_phone = ""` → clear về null (mirror business_phone).
- `owner_phone` không hợp lệ → `AssertPhoneNumber` chặn ở input validation.
- User là Fastboy employee (không reseller) → bị gate, không set được (giống business_phone hiện tại) — đúng behavior mong muốn.

## Checklist
- [ ] `User.ownerPhone` + getter/setter.
- [ ] Migration ADD `owner_phone` (diff + dọn drift + review).
- [ ] `UpdateMutation/Input.php` field `owner_phone`.
- [ ] `UpdateMutation/Resolver.php` parse + truyền.
- [ ] `UserService::patchUser` param + set (trong gate).
- [ ] Hasura `public_user.yaml` cột `owner_phone` (2 role) + apply.
- [ ] SDL `user_update_mutation_input` thêm `owner_phone` + apply.
- [ ] `doctrine:schema:validate` in sync; lint sạch.

## Verify (sau implement)
- `user_update(input_obj: { owner_phone: "+8493..." })` → query Hasura `user { owner_phone }` thấy đúng giá trị.
- Gửi `owner_phone: ""` → giá trị về null.
- `owner_phone` sai format → reject validation.
- User reseller set được; (nếu test) Fastboy employee bị gate.
