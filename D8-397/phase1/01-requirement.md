# Task: Bổ sung field referral_order.resell_type

## Description
- Phần này cần làm trước
- Tôi cần bổ sung field `resell_type` cho table `referral_order` 
```
enum ResellType: string
{
    case PURCHASE_TO_INVENTORY = 'purchase_to_inventory';  
    case SELL_FROM_INVENTORY = 'sell_from_inventory';  
    case SELL_VIA_CRM = 'sell_via_crm';
}
```
- sell_via_crm -> default, nếu không truyền thì lấy value này
- purchase_to_inventory -> agent mua về inventory của họ
- sell_from_inventory -> hiện tại chưa cần hỗ trợ, chỉ tạo sẵn value này thôi

- API create order thêm field resell cho phép bỏ trống, BE default lấy sell_via_crm lưu vào field
, &
## Q&A
> Hãy Q&A để làm rõ requirement

1. **Quan hệ với tech-spec cũ (`order_type`/`agent_purchase`)?**
   → `resell_type` **thay thế hoàn toàn** `order_type`. Mapping: `purchase_to_inventory` = `agent_purchase` cũ; `sell_via_crm` = `client_order`/null cũ.

2. **Hiện thực enum + map Doctrine thế nào?**
   → Dùng **const string** trong entity (theo convention repo: `STATUS_*`, `CARD_TYPES`), **không** tạo PHP backed enum thật. Column `type: 'string'`.

3. **Cột DB nullable & backfill?**
   → **Nullable, không backfill**. `null` (đơn cũ) được code hiểu là `sell_via_crm` qua `getEffectiveResellType()`. Đơn mới luôn được application set value khi create.

4. **Phạm vi phase1?**
   → Chỉ **thêm field + persist + default + expose**. CHƯA đụng business logic (skip client/commission/stock cho purchase_to_inventory để phase sau).

## Notes
- Const: `ReferralOrder::RESELL_TYPE_SELL_VIA_CRM`, `RESELL_TYPE_PURCHASE_TO_INVENTORY`, `RESELL_TYPE_SELL_FROM_INVENTORY`, list `RESELL_TYPES`.
- Default `sell_via_crm` set trong `ReferralOrderService::create()` (`$resellType ?? RESELL_TYPE_SELL_VIA_CRM`) → đơn tạo qua mutation luôn có value, không null.
- Migration `Version20260617072022` (diff): `ADD resell_type VARCHAR(32) DEFAULT NULL` + `idx_referral_order_resell_type`. Đã chạy & verify trên dev.
- **Follow-up**: cần **reload Hasura metadata** để cột `resell_type` được track/expose qua Hasura gateway.
- ECS hiện lỗi config sẵn có (old `ContainerConfigurator`) — không chạy được, không liên quan thay đổi này.