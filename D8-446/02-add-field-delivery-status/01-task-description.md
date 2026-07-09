## Task Description
- referral_order bổ sung field: delivery_status - string, nullable
- enum gồm những values sau
enum TrackingStatus: string
{
    case LABEL_CREATED = 'label_created';
    case PICKED_UP = 'picked_up';
    case IN_TRANSIT = 'in_transit';
    case OUT_FOR_DELIVERY = 'out_for_delivery';
    case DELIVERED = 'delivered';
    case EXCEPTION = 'exception';
    case UNKNOWN = 'unknown';
}

## Q&A — Đã chốt

1. **Mapping**: cột `delivery_status` map bằng `#[ORM\Column(type: Types::STRING, enumType: TrackingStatus::class, nullable: true)]` → property `?TrackingStatus`. DB vẫn lưu string nullable. (Khác convention cũ: field `status` đang plain string — nhưng dùng enumType theo yêu cầu.)
2. **Enum**: `enum TrackingStatus: string` đặt cạnh entity → namespace `App\Entity\ReferralOrder\TrackingStatus`. 7 case: label_created, picked_up, in_transit, out_for_delivery, delivered, exception, unknown.
3. **Scope**: (a) thêm property + getter/setter vào `ReferralOrder` + tạo enum, (b) `doctrine:migration:diff`, (c) expose qua GraphQL `ReferralOrderEntityType` (`#[GraphQL\SourceField] delivery_status`, outputType String).