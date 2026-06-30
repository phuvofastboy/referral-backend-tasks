# D8-426 — Credit values: cách lấy `credit`, `reserved_credit`, `available_credit`

> Mô tả 3 giá trị credit của một reseller và cách lấy từ **hàm có sẵn**. Dùng làm tham chiếu khi implement resolver (chi tiết resolver sẽ yêu cầu sau).
> Tên field ở đây là **đề xuất** (chưa chốt): `reserved_credit` khớp thuật ngữ "reserve" trong code (`CREDIT_RESERVE_STATUSES`, `sumReservedCreditUse`); phương án khác: `held_credit` / `credit_on_hold`.

## 1. Ba giá trị & quan hệ

```
available_credit = credit − reserved_credit
```

| Giá trị | Ý nghĩa | Nguồn |
|---|---|---|
| `credit` | Số dư credit gốc của reseller (scalar) | `User.credit` |
| `reserved_credit` | Tổng credit đang bị "giữ" bởi các đơn chưa settle (đã nhập `credit_use` nhưng chưa paid/đã hủy) | SUM `credit_use` các đơn ở reserve-status |
| `available_credit` | Số credit còn dùng được cho đơn mới | `credit − reserved_credit` |

## 2. Hàm có sẵn

### 2.1 `credit` — số dư gốc
`App\Entity\User\User`
```php
public function getCredit(): ?float   // src/Entity/User/User.php:603
```
- Cột `user.credit` (float, nullable, mặc định null). Có thể âm (đã cho phép — reseller âm là cờ rà soát DB).
- Lấy trực tiếp: `$user->getCredit() ?? 0.0`.

### 2.2 `reserved_credit` — credit đang hold
`App\Repository\ReferralOrder\ReferralOrderRepository`
```php
public function sumReservedCreditUse(User $user, ?string $excludeOrderId = null): float   // :28
```
- Trả về `COALESCE(SUM(o.credit_use), 0)` với các đơn:
  - `created_by = $user`
  - `credit_use > 0`
  - `status IN` **`ReferralOrder::CREDIT_RESERVE_STATUSES`** (`ReferralOrder.php:119`):
    `draft, sent, viewed, signed, pending_payment, decline_payment`
  - (tùy chọn) loại `id = $excludeOrderId` — dùng khi đang update chính đơn đó để không tự đếm.
- Đơn `paid` (đã trừ thật), `cancelled`, `client_rejected` **không** tính (credit đã trừ hoặc đã thả).
- Gọi: `$referralOrderRepository->sumReservedCreditUse($user)`.

### 2.3 `available_credit` — số dùng được
`App\Service\Credit\CreditService`
```php
public function availableCredit(User $user, ?ReferralOrder $excludeOrder = null): float   // :26
```
- Công thức: `round((float) $user->getCredit() - sumReservedCreditUse($user, $excludeOrder?->id), 2)`.
- Đây là hàm dùng khi validate `credit_use` lúc create/update (đã tích hợp trong `ReferralOrderService::applyCreditUse`).
- Gọi: `$creditService->availableCredit($user)`.

## 3. Lấy cả 3 giá trị (gợi ý cho resolver sau này)

```php
$credit    = round((float) $user->getCredit(), 2);
$reserved  = $this->referralOrderRepository->sumReservedCreditUse($user);
$available = $this->creditService->availableCredit($user);   // = $credit - $reserved
```
> `available` đã tự trừ `reserved`, không cần trừ lại. Nếu muốn tránh 2 lần query SUM, có thể tính `available = $credit - $reserved` từ 2 giá trị trên.

## 4. Điểm CHƯA chốt (cần xác nhận khi implement resolver)
- **Tên field**: `reserved_credit` vs `held_credit` vs `credit_on_hold`.
- **Surface**: GraphQL query cho FE, service-only, hay cả hai.
- **Input/quyền**: current user (InjectUser) hay nhận `user_id` (xem reseller khác → cần guard role).
- **Shape**: trả 1 object `{ credit, reserved_credit, available_credit }` hay chỉ `{ reserved_credit, available_credit }` (vì `credit` đã có sẵn trên user type).
- **(Nếu cần method `reservedCredit` trong CreditService)**: hiện logic nằm ở repository `sumReservedCreditUse`; có thể thêm wrapper `CreditService::reservedCredit(User)` cho đối xứng với `availableCredit(User)`.
