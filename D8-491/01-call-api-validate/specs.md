# Spec — D8-491 / 01: API validate order từ CRM (chặn trước khi charge)

> **STATUS: SPEC** — chưa implement. Repo liên quan: `referral-backend` (repo này) + `crm-backend`.
> **Thứ tự deploy: CRM trước, referral sau.** Xem §7.

---

## 1. Vấn đề

CRM có một luật nghiệp vụ về `start_date` của line service mà **referral không biết**, và luật đó chỉ
chạy **sau khi khách đã trả tiền**.

### 1.1 Luật ở CRM

`crm:src/Service/Referral/Order/OrderService.php:736` — `assertResellServiceDoesNotOverlap()`, gọi ở
`:639` trong `prepareFinInvoiceByReferralOrder()`:

1. Chỉ chạy khi match được `ClientCompany` — `findBy` theo **đúng 5 field địa chỉ**
   (`address1`, `city`, `state`, `country`, `postal_code`), `createdAt DESC`, lấy row đầu
   (`crm:.../OrderService.php:587-600`). Khách mới không match → `return`, không check gì.
2. Bỏ qua line không có `start_date` (device/merchant — `resolveResellServiceStartDate()` ở `:702`
   trả `null` khi `product_type !== service`).
3. `ClientCompanyProductRepository::getMaxNextDueDateServiceActive($product, $companyId)`
   (`crm:src/Repository/Client/ClientCompanyProductRepository.php:547`) — lọc
   `status IN (active, pending)` + `expiration_date IS NOT NULL`, `ORDER BY expiration_date DESC`,
   lấy row đầu.
4. Nếu `start_date < expiration_date` → throw:
   ```
   Company already has an active "%s" until %s. Start date must be %s or later.
   ```
   `until = expiration_date - 1 day`, `must be = expiration_date`. Tức `expiration_date` là ngày
   **sau** ngày hết kỳ, nên start **đúng bằng** nó (gia hạn liền kỳ) là hợp lệ.

### 1.2 Vì sao đây là bug, không chỉ là "thiếu validate"

Chuỗi gọi: `prepareFinInvoiceByReferralOrder` ← `createOrderCrm()` (`:312`) ← `syncOrder()` (`:85`)
← `crm:src/MessageHandler/Referral/Order/OrderRequestHandler.php:48`, **chỉ chạy khi
`status === PAID`** (dispatch từ `crm:src/GraphQL/Payment/Resell/UpdateOrderStatus/Resolver.php:39`).

Và handler đó:

```php
try {
    $this->orderService->syncOrder($orderId, $internalId, $resellType);
} catch (\Exception $e) {
    $this->logger->error($e->getMessage());   // nuốt: không rethrow, không vào failed transport
}
```

Failure mode thực tế: **khách bị charge → order `paid` ở referral → CRM không tạo `FinInvoice`,
không tạo `ClientCompanyProduct` → chỉ còn một dòng log.** Không retry được bằng
`messenger:failed:retry` vì message không hề fail.

(Đường CRM UI thì check sớm — `crm:src/Validator/FinInvoice/ValidateInvoiceApplyPromotionConstraintValidator.php:394`
— nhưng validator đó sống trên Input DTO của mutation, đường referral gọi thẳng
`FinOrderService::createOrder()` nên bỏ qua toàn bộ, đúng như ADR-0017 §"Vì sao phải chặn hai đầu".)

### 1.3 Referral hiện có gì

Grep `overlap|ClientCompanyProduct|expirationDate|NextDueDate|client_company_product` trong
`app/src` + `app/graphql` → **0 hit**. Referral chỉ có 2 check về `start_date`, cả hai trong
`ReferralOrderService::applyCrmProductData` và **chỉ khi `$isSubmit`** (`status === sent`):

| Vị trí | Check | Message |
| --- | --- | --- |
| `app/src/Service/ReferralOrder/ReferralOrderService.php:539` | termed line thiếu `start_date` | `Start date is required for service product %s` |
| `app/src/Service/ReferralOrder/ReferralOrderService.php:548` | `computeEndDate()` trả null | `Service product %s has no billable term (...)` |

`ReferralOrderProductInput::$startDate` (`app/src/GraphQL/ReferralOrder/ReferralOrderProductInput.php:76`)
**không có constraint nào** — không `NotBlank`, không `Type`, không group `onSubmitOrder`.

`preview` không truyền status (`PreviewOrder/Resolver.php:185` gọi `create()` với `$status = null`
→ `STATUS_DRAFT`), nên trên đơn mới **cả 2 check trên cũng không chạy ở preview**.

### 1.4 Lỗ hổng thứ hai: chồng lấn trong cùng một cart

`getMaxNextDueDateServiceActive` chỉ thấy `ClientCompanyProduct` **đã tồn tại**. Hai line cùng một
service trong **cùng một đơn** với kỳ hạn chồng nhau đều là line mới → không ai bắt, ở cả hai repo.

---

## 2. Quyết định

**Thêm một query mới ở CRM: `referral_order_validate`. Referral gửi thông tin company + danh sách
line, CRM trả về danh sách violation. Referral chặn ở submit, warn ở preview.**

### 2.1 Vì sao API mới, không phải referral tự check

- **Không phải mở quyền đọc `client_company_product`.** Bảng đó hiện **không** có
  `ROLE_HASURA_REFERRAL` (`crm:hasura/metadata/sources/default/tables/public_client_company_product.yaml`
  — chỉ có `ROLE_AM`, `ROLE_BILLING`, `ROLE_SALE`, …; so với `public_client_company.yaml:1180` thì
  có). Mở select permission = mở lịch sử subscription của khách cho referral đọc tự do. Một query
  trả về đúng câu trả lời thì hẹp hơn nhiều.
- **Luật ở lại một chỗ.** `expiration_date` là "ngày sau ngày hết kỳ", `status IN (active, pending)`,
  lấy `MAX` — referral implement lại là drift chờ sẵn. Đã có tiền lệ: `computeEndDate()` của referral
  phải mirror công thức CRM và **đến giờ vẫn lệch** — CRM có nhánh `isSetEndMonth`
  (`crm:src/Service/Fin/FinInvoiceProductService.php:464`) mà referral không có.
- **Có tiền lệ trong repo.** `crm_referral_order_get_product_taxed`
  (`crm:src/GraphQL/Referral/Order/GetProductTaxed/Resolver.php:47`) và `crm_check_product_stock`
  đúng khuôn này: referral gửi cart, CRM trả phán quyết.

### 2.2 Input là cart, không phải `referral_order_id`

`PreviewOrder/Resolver.php` **không** `flush()` và **không** `#[Transactional]` — đơn preview mới
chưa có id. API nhận `order_id` thì preview đơn mới không gọi được, mà đó chính là ca cần chặn sớm
nhất.

### 2.3 Company: chỉ nhận 5 field địa chỉ, CRM tự match

**Không nhận `client_company_id`.** Hai lý do:

1. Referral không có sẵn id đó. `Company` của referral giữ `whmcs_id` (ADR-0018), không phải uuid
   `client_company` của CRM. Muốn có id thì phải gọi thêm
   `graphql/Crm/GetCrmClientCompanyByAddressInfo.graphql` — thêm một roundtrip và thêm một bản copy
   của matcher.
2. Nhận id là mở một oracle enumeration: "company X có đang dùng product Y không". Gửi địa chỉ thì
   caller chỉ hỏi được về địa chỉ mà chính nó đang dựng đơn cho.

Để CRM tự `findBy` bằng matcher của chính nó ⇒ **validate và sync không thể land trên hai company
khác nhau** — đúng lý lẽ đã ghi trong comment của `GetCrmClientCompanyByAddressInfo.graphql`.

Mở rộng sau (nếu cần): thêm `client_company_id` **kèm** ownership check, không phải thay thế.

### 2.4 Fail-open ở preview, fail-closed ở submit

Repo đã có sẵn idiom đúng — `queryCheckProductStock($changeSet, $isSubmit)`
(`ReferralOrderService.php:321`), tham số thứ hai là `$isThrowException`. Dùng lại y hệt:

| Ngữ cảnh | CRM trả violation | CRM lỗi/timeout |
| --- | --- | --- |
| preview / draft | warn, không chặn | log + đi tiếp (fail-open) |
| submit (`status = sent`) | **throw** | **throw** (fail-closed) |

Lý do fail-closed ở submit: failure mode phía sau là §1.2 — khách bị charge, không có invoice, một
dòng log. Chặn ồn ào rẻ hơn nhiều.

### 2.5 GIỮ NGUYÊN guard ở sync

`assertResellServiceDoesNotOverlap` ở `prepareFinInvoiceByReferralOrder` **không được xoá**. API mới
là chốt *sớm*, không thay chốt *cuối*: giữa lúc preview và lúc paid, kỳ hạn có thể bị một đơn khác
(hoặc một đơn CRM UI) ăn mất. Đúng nguyên tắc "chặn hai đầu" của ADR-0017.

Refactor: tách luật ra service dùng chung, **cả hai** đường gọi cùng một service (§4.2).

### 2.6 Tính mở rộng nằm ở `code`, không nằm ở shape

Yêu cầu "API sau này mở rộng validate thêm nhiều thông tin khác" giải bằng cách:

- Output là **danh sách violation**, mỗi violation có `code` (string) + `severity` + `message` +
  `product_id` + `data` (jsonb).
- Thêm luật mới = thêm một giá trị `code` mới. CRM deploy một mình, **referral không sửa một dòng
  nào**, FE vẫn hiện đúng message.
- Nếu ngược lại — mỗi luật mới thêm một field vào output — thì lần nào cũng phải
  `hasura:metadata:persist` → `export` → commit ở **cả hai** repo.

⚠️ **Referral không được `switch` vét cạn trên `code`.** Code lạ → hiển thị `message`, tin
`severity`. Đây là contract, không phải khuyến nghị.

⚠️ **`severity` là `String`, không phải GraphQL enum.** Thêm giá trị enum mới buộc phải regenerate
remote-schema SDL ở `crm:hasura/metadata/remote_schemas/local/permissions/role_rolehasurareferral.yaml`
— đúng cái friction đang muốn tránh.

### 2.7 Output typed, không phải `jsonb` trần

`referral_order_get_product_taxed` trả `jsonb`; **cái này không nên**. Referral phải đọc `severity`
để quyết chặn hay không, tức nó nằm trên money path — sai một key trong jsonb là `?? null` im lặng
→ chặn hụt. Envelope typed + `data` là jsonb cho phần payload tự do là cân bằng đúng.

Đã có tiền lệ typed output mở cho role referral:
`fin_fin_invoice_promotion_get_apply_promotion_output`
(`crm:src/GraphQL/Fin/FinInvoicePromotion/GetApplyPromotion/Output.php:18`).

### 2.8 Việc này đảo một quyết định của ADR-0017 → phải viết ADR

ADR-0017 §Considered Options ghi:

> **Referral pre-check chồng lấn qua query CRM mới** — loại khỏi v1: chỉ chạy được khi
> `Company.whmcs_id` có giá trị, mà company sinh từ đơn thì `whmcs_id` NULL theo thiết kế (ADR-0015)
> ⇒ check không đáng tin.

**Lý do đó hết đúng**: CRM không match bằng `whmcs_id`, nó match bằng tuple địa chỉ
(`crm:.../OrderService.php:587-600`). Viết ADR mới trong `docs/adr/`, hoặc amend 0017 theo đúng khuôn
RES-13 đã dùng cho mục Promotion (`> **Superseded (D8-491) — chỉ mục này.** …`), ghi lại vì sao lý do
cũ hết hiệu lực. Bên `crm-backend/docs/adr/` cũng cần một bản tương ứng cho query mới.

---

## 3. Contract

### 3.1 Input

```graphql
input referral_order_validate_line_input {
    product_id: String!      # CRM product uuid
    quantity: Int!           # = số kỳ với line service (ADR-0017), không phải số bản
    start_date: String       # RFC3339 hoặc Y-m-d; null với line không phải service
}

input referral_order_validate_input {
    # 5 field địa chỉ — CRM tự match ClientCompany bằng matcher của chính nó (§2.3).
    # Tất cả rỗng ⇒ khách mới ⇒ bỏ mọi check gắn với company.
    address: String
    city: String
    state: String
    country: String
    postal_code: String

    resell_type: String                                 # rule tương lai sẽ cần
    list_product: [referral_order_validate_line_input!]!
}
```

### 3.2 Output

```graphql
type referral_order_validate_violation_output {
    code: String!            # xem §3.3
    severity: String!        # 'error' | 'warning'
    message: String!         # đã format sẵn, FE hiện thẳng
    product_id: String       # null nếu violation ở cấp đơn
    data: jsonb              # payload tự do, tuỳ code
}

type referral_order_validate_output {
    is_valid: Boolean!       # = không có violation nào severity='error'
    violations: [referral_order_validate_violation_output!]!
}
```

`is_valid` là derived nhưng vẫn trả: nó đặt **policy severity ở CRM** — một chỗ duy nhất. Referral
chặn submit khi `is_valid === false`, không tự tính lại.

### 3.3 Registry `code` (v1)

| code | severity | product_id | `data` | Nguồn luật |
| --- | --- | --- | --- | --- |
| `RESELL_SERVICE_TERM_OVERLAP` | `error` | có | `{ current_term_end, earliest_start_date, product_name }` | `assertResellServiceDoesNotOverlap` (đang có, chuyển vào service dùng chung) |
| `RESELL_SERVICE_START_DATE_REQUIRED` | `error` | có | `{ product_name }` | `resolveResellServiceStartDate` (`crm:...:702`) |
| `RESELL_SERVICE_NO_BILLABLE_TERM` | `error` | có | `{ circle_billing, expiry }` | `getNumberOfMonth()` trả 0 (`crm:src/Service/Product/ProductService.php:633`) |
| `RESELL_SERVICE_TERM_OVERLAP_IN_CART` | `error` | có | `{ conflicting_line_index, overlap_from, overlap_to }` | **mới** — §1.4 |

`message` của `RESELL_SERVICE_TERM_OVERLAP` giữ **nguyên văn** câu hiện tại để log/ticket cũ vẫn
grep được:
`Company already has an active "%s" until %s. Start date must be %s or later.` (`m/d/Y`).

---

## 4. Implement phía CRM (`crm-backend`)

### 4.1 File mới

```
crm-backend/src/GraphQL/Referral/Order/Validate/
├── Input.php               # referral_order_validate_input
├── LineInput.php           # referral_order_validate_line_input
├── ViolationOutput.php     # referral_order_validate_violation_output
├── Output.php              # referral_order_validate_output
└── Resolver.php            # query referral_order_validate
```

Theo khuôn `crm:src/GraphQL/Referral/Order/GetProductTaxed/` (cùng namespace parent, cùng role).

**Input** — `#[GQL\Input(name: '...', default: true)]`, field snake_case:

```php
#[GQL\Input(name: 'referral_order_validate_input', default: true)]
final class Input
{
    #[GQL\Field(name: 'address', inputType: 'String')]
    public ?string $address = null;

    // city / state / country / postal_code: y hệt

    #[GQL\Field(name: 'resell_type', inputType: 'String')]
    public ?string $resellType = null;

    /** @var LineInput[]|null */
    #[GQL\Field(name: 'list_product', inputType: '[referral_order_validate_line_input!]!')]
    #[Assert\Valid]
    public ?array $listProduct = null;
}
```

**Output** — theo khuôn `crm:src/GraphQL/Fin/FinInvoicePromotion/GetApplyPromotion/Output.php`:
`#[Type(name: ...)]` + static `factory()` + getter có `#[Field(name: ..., outputType: ...)]`.
`data` khai `outputType: 'jsonb'`.

**Resolver**:

```php
#[GQL\Query(name: 'referral_order_validate')]
#[Roles('ROLE_HASURA_REFERRAL')]
public function __invoke(
    #[ArgNaming(name: 'input_obj')] #[ObjectAssertion] Input $inputObj,
): Output
```

`#[ArgNaming]` / `#[ObjectAssertion]` đặt **trên parameter**, không phải method — dạng method-level đã
deprecated (xem CLAUDE.md §Coding Patterns).

### 4.2 Tách luật ra service dùng chung

Tạo `crm-backend/src/Service/Referral/Order/OrderValidationService.php`:

```php
/**
 * @param LineInput[]|array<int, array<string,mixed>> $lines
 * @return ViolationOutput[]
 */
public function validate(array $lines, ?ClientCompany $company, ?string $resellType): array
```

Chứa toàn bộ 4 luật ở §3.3. **Read-only tuyệt đối**:

- ⚠️ **KHÔNG** gọi lại `prepareFinInvoiceByReferralOrder()` — hàm đó `$this->em->flush()` ở cuối
  (`crm:.../OrderService.php:657`). Đây đúng bài học `apply_promotion` tạo `FinInvoice` thật mà
  ADR-0017 §"Vì sao hoãn Promotion" đã ghi.
- **KHÔNG** `flush()`, **KHÔNG** `persist()` trong service này và trong resolver.
- Là `#[GQL\Query]`, không phải `Mutation`.

Rồi **rewire** `assertResellServiceDoesNotOverlap()` (`:736`) gọi service này thay vì tự làm — nó
throw khi service trả về violation `severity = error`. Như vậy sync và validate không thể lệch luật.
Giữ nguyên signature + message của nó để không phá caller/test hiện có.

Công thức end date của line đề nghị (cho check in-cart, §1.4):
`numberOfMonth = ProductService::getNumberOfMonth($product) × quantity`, rồi
`end = start + numberOfMonth month - 1 day` — **đúng** `crm:src/Service/Fin/FinInvoiceProductService.php:462`.
Không tự viết công thức mới.

### 4.3 Hasura metadata

```bash
php bin/console hasura:metadata:persist   # #[Roles] → remote schema permissions (ghi vào Hasura live)
php bin/console hasura:metadata:export    # → hasura/metadata/**
git add hasura/metadata && git commit
```

`persist` **chỉ** ghi vào instance live, không đụng yml. Quên `export` + commit thì deploy sẽ mất
permission và referral ăn `field not found`.

File sẽ đổi:
`crm-backend/hasura/metadata/remote_schemas/local/permissions/role_rolehasurareferral.yaml` —
thêm dòng `referral_order_validate(input_obj: referral_order_validate_input!): referral_order_validate_output!`
vào block `query_root` (hiện `referral_order_get_product_taxed` ở dòng 32), kèm các `type` / `input`
mới ở phần SDL bên dưới.

### 4.4 Sau khi thêm class mới

`composer dump-autoload` (classmap-authoritative — CLAUDE.md), nếu không sẽ ăn
`Expected to find class "App\..." in file ... but it was not found`.

---

## 5. Implement phía Referral (`referral-backend`)

### 5.1 File mới

```
app/graphql/Crm/ValidateReferralOrder.graphql
app/src/Service/Crm/OrderValidationService.php
```

### 5.2 `.graphql` — CHỈ MỘT VARIABLE

⚠️ **Gotcha bắt buộc**: Hasura đặt tên mọi variable nó forward sang remote schema là
`hasura_json_var_N` và **dùng lại index 1** ngay khi có variable thứ hai → CRM reject query với
`There can be only one variable named "hasura_json_var_1"`. Gộp toàn bộ vào **một** variable
(xem comment đầu `app/graphql/Crm/GetPromotionPreview.graphql`).

```graphql
query ValidateReferralOrder($input: crm_referral_order_validate_input!) {
    crm_referral_order_validate(input_obj: $input) {
        is_valid
        violations {
            code
            severity
            message
            product_id
            data
        }
    }
}
```

Prefix `crm_` do remote-schema customization của referral (`app/hasura/metadata/remote_schemas.yaml:71-137`).

**Không cần đổi** `app/hasura/metadata/remote_schemas/crm/permissions.yaml`: file đó chỉ liệt kê
`roleanonymous` / `roleuser` / `rolehasuracrm` cho FE, còn `GraphqlClient` đi bằng admin secret
(`app/config/packages/hasura.yaml:3`) nên bypass permission.

⚠️ Sau khi CRM deploy: phải **reload remote schema `crm`** ở Hasura của referral, nếu không field mới
không tồn tại trong schema và query fail dù CRM đã có.

### 5.3 `Service/Crm/OrderValidationService.php`

Theo khuôn `app/src/Service/Crm/PromotionService.php`:

```php
/**
 * @param array<int, array{product_id: string, quantity: int, start_date: ?string}> $lines
 * @return array{is_valid: bool, violations: array<int, array<string, mixed>>}
 */
public function validate(ReferralOrder $order, array $lines): array
{
    $response = $this->graphqlClient->queryFromFile('Crm/ValidateReferralOrder', [
        'input' => [
            'address'      => ..., 'city' => ..., 'state' => ...,
            'country'      => ..., 'postal_code' => ...,
            'resell_type'  => $order->getEffectiveResellType(),
            'list_product' => $lines,
        ],
    ]);

    GraphqlErrorsException::throwExceptionFromResponse($response);

    return $response['data']['crm_referral_order_validate'] ?? [];
}
```

**Địa chỉ lấy từ đâu**: `$order->getCompany() ?? $order->getNewClientInfo()`, đúng 5 field như
`PromotionService::resolveClientCompany()` (`app/src/Service/Crm/PromotionService.php:154-170`).
`resolveClientCompany()` đang `private` → **tách phần dựng address tuple ra chỗ dùng chung** (helper
static hoặc method public trên `PromotionService`) rồi cả hai gọi. Đừng copy — đó sẽ là bản thứ ba
của cùng một matcher.

Giữ nguyên guard "địa chỉ rỗng hoàn toàn ⇒ coi là khách mới" của `resolveClientCompany()`: gửi 5
field rỗng sang CRM sẽ `_eq`-match company nào tình cờ có cột địa chỉ rỗng.

### 5.4 Call site

Trong `ReferralOrderService::applyCrmProductData`, **ngay sau** `classifyProductLines`
(`app/src/Service/ReferralOrder/ReferralOrderService.php:313`) và **trước** vòng `foreach` dựng line:

```php
['stockless' => $stocklessProductIds, 'termed' => $termedProductIds]
    = $this->classifyProductLines($order, $productList, $crmProducts);

// D8-491: CRM sở hữu luật kỳ hạn (chồng lấn với gói đang active, chồng lấn trong cùng cart).
// Chỉ hỏi khi cart có line termed — cart toàn device không phát sinh roundtrip nào.
if ($termedProductIds !== []) {
    $this->assertOrderValidByCrm($order, $productList, $termedProductIds, $isSubmit);
}
```

Đặt ở đây vì:
- `applyCrmProductData` là đường chung của **cả** `create`, `update` **và** `preview` — một chỗ sửa,
  ba mutation được bảo vệ.
- `$termedProductIds` đã có sẵn từ `classifyProductLines`, không phải classify lại.
- Trước khi dựng line ⇒ fail trước khi ghi bất cứ thứ gì vào entity.

Hành vi (§2.4):

```php
public function assertOrderValidByCrm(..., bool $isSubmit): void
{
    try {
        $answer = $this->queryOrderValidation($order, $lines);   // seam, xem §5.5
    } catch (\Throwable $e) {
        if ($isSubmit) {
            throw new GraphQLException('Cannot validate order with CRM: ' . $e->getMessage());
        }
        $this->logger->warning('CRM order validation unavailable, preview continues', [...]);
        return;                                                   // fail-open ở preview
    }

    $errors = array_filter(
        $answer['violations'] ?? [],
        static fn (array $v): bool => ($v['severity'] ?? 'error') === 'error',
    );

    if ($isSubmit && ($answer['is_valid'] ?? false) === false) {
        // message của CRM đã format sẵn — nối lại, KHÔNG dịch, KHÔNG switch trên `code`
        throw new GraphQLException(implode(' ', array_column($errors, 'message')));
    }
    // preview: chỉ warn — xem §5.6
}
```

⚠️ `($v['severity'] ?? 'error')` — mặc định là `error`, không phải `warning`. Severity lạ/thiếu phải
nghiêng về chặn, không nghiêng về cho qua.

⚠️ Không `switch`/`match` vét cạn trên `code` (§2.6). Chỉ đọc `severity` + `message`.

### 5.5 Seam để test

Bọc HTTP call trong một method **public** trên `ReferralOrderService`, đúng khuôn
`queryProductTax()` / `queryCheckProductStock()` / `queryPromotionAnswer()`
(`app/src/Service/ReferralOrder/ReferralOrderService.php:1113`):

```php
/**
 * Seam over the CRM order-validation call, same shape as queryProductTax()/queryCheckProductStock().
 */
public function queryOrderValidation(ReferralOrder $order, array $lines): array
{
    return $this->orderValidationService->validate($order, $lines);
}
```

Bắt buộc, vì `tests/Service/ReferralOrder/ReferralOrderServicePricingTest.php:25-70` dùng subclass
`TestableReferralOrderService` override đúng các method seam này. Không có seam thì mọi test hiện
có của `applyCrmProductData` sẽ đi gọi HTTP thật và đỏ hàng loạt.

Mặc định trong subclass test: trả `['is_valid' => true, 'violations' => []]`.

### 5.6 Trả warning về FE ở preview

`referral_order_entity_type` hiện không có field nào chở warning. Dùng lại khuôn
`attachPromotionAnswer()` (`app/src/GraphQL/ReferralOrder/Mutation/PreviewOrder/Resolver.php:206`) —
nó đã giải đúng bài "preview cần trả thêm thông tin CRM ngoài entity".

Nếu scope cần gọn, v1 có thể **chỉ log** ở preview và chặn ở submit; nhưng như vậy reseller vẫn chỉ
biết lỗi ở bước cuối. Nên làm luôn.

### 5.7 Giữ nguyên 2 check cũ

`:539` và `:548` **không xoá**. Chúng chạy được khi CRM không với tới (fail-open ở preview) và là
defense in depth, không phải trùng lặp cần dọn.

### 5.8 Sau khi thêm class mới

`composer dump-autoload` (memory `graphqlite-new-resolver-gotchas`).

---

## 6. Ngoài scope task này — nhưng phải mở ticket

**`OrderRequestHandler` nuốt exception** (`crm:src/MessageHandler/Referral/Order/OrderRequestHandler.php:48-53`).
Kể cả pre-check hoàn hảo, race vẫn tồn tại — hai đơn cùng company + cùng product paid gần nhau, hoặc
một đơn CRM UI chen vào giữa preview và paid — và vẫn rơi vào đúng nhánh `catch` này: khách đã trả
tiền, không có invoice, không retry, một dòng log. Cho nó throw (vào failed transport,
`messenger:failed:retry` được) hoặc ghi một trạng thái fail nhìn thấy được về referral.

Theo tôi cái này **gấp hơn** API mới: API mới giảm tần suất, cái này quyết định điều gì xảy ra khi
vẫn lọt.

**`isSetEndMonth` lệch giữa hai repo** (`crm:.../FinInvoiceProductService.php:464` vs
`ReferralOrderProduct::computeEndDate()`): snapshot `end_date` referral in lên document e-sign có thể
lệch `FinInvoiceProduct.end` của CRM. Chưa rõ đường referral có bật flag đó hay không (default
`false`, `UpdateOrder.php:341` truyền `false`) — cần xác nhận rồi hoặc mirror, hoặc ghi lại là cố ý.

---

## 7. Thứ tự deploy

**CRM trước, referral sau** — ngược với luật mặc định "referral trước" của ADR-0017, cùng lý do
ADR-0019 đã phải đảo một lần:

1. CRM: merge + deploy + `hasura:metadata:apply` (helm job).
2. Reload remote schema `crm` ở Hasura của referral.
3. Verify `crm_referral_order_validate` có trong schema của referral (§8.1).
4. Referral: merge + deploy.

Nếu referral ra trước: mọi cart có line service sẽ gọi một field không tồn tại → GraphQL error →
fail-closed ở submit → **chặn sạch đơn service**. Preview thì fail-open nên chỉ log.

---

## 8. Test

### 8.1 Smoke (dev)

```bash
# Trong container apache của referral, gọi thẳng Hasura của referral (admin) để xác nhận field tồn tại
php bin/console dbal:run-sql "SELECT 1"   # sanity container

# Query qua GraphqlClient — hoặc curl trực tiếp Hasura :8080 với admin secret ilovefastboy
```

Dùng skill `local-test-graphql-api` cho `referral_order_preview_order` / `referral_order_create` với
một line service.

### 8.2 Unit (referral) — `tests/Service/ReferralOrder/`

Thêm vào `TestableReferralOrderService` override `queryOrderValidation()`, rồi test:

| Case | Kỳ vọng |
| --- | --- |
| cart toàn device | `queryOrderValidation` **không** được gọi (assert counter = 0) |
| cart có line service, CRM trả `is_valid: true` | pass, line dựng bình thường |
| submit + CRM trả violation `severity: error` | `GraphQLException`, message chứa message của CRM |
| submit + CRM trả violation `severity: warning` | pass (không chặn) |
| submit + CRM trả `severity` lạ/thiếu | **chặn** (default `error`) |
| preview + CRM trả violation error | pass, không throw |
| preview + `queryOrderValidation` throw | pass, có log warning |
| submit + `queryOrderValidation` throw | `GraphQLException` |

### 8.3 Unit (CRM)

- `OrderValidationService`: 4 luật ở §3.3, mỗi luật một case + case company null (khách mới → không
  violation nào gắn company).
- Boundary quan trọng: `start_date === expiration_date` ⇒ **hợp lệ** (gia hạn liền kỳ);
  `start_date === expiration_date - 1 day` ⇒ violation.
- Regression: `assertResellServiceDoesNotOverlap()` sau rewire vẫn throw đúng message cũ.

### 8.4 Baseline

`./scripts/test.sh` — baseline referral là **350 test / 859 assertion, xanh hoàn toàn**. Có gì đỏ là
do thay đổi này.

`SchemaSnapshotTest` sẽ **không** đổi (không thêm field vào schema GraphQL của referral). Nếu nó đỏ
thì có thứ gì bị sửa ngoài ý muốn — đừng regenerate snapshot cho xanh.

---

## 9. Checklist

**CRM (`crm-backend`)**
- [ ] `src/GraphQL/Referral/Order/Validate/{Input,LineInput,ViolationOutput,Output,Resolver}.php`
- [ ] `src/Service/Referral/Order/OrderValidationService.php` (read-only, không flush)
- [ ] Rewire `assertResellServiceDoesNotOverlap()` → gọi service mới, giữ message cũ
- [ ] Unit test 4 luật + boundary `start == expiration_date`
- [ ] `composer dump-autoload`
- [ ] `hasura:metadata:persist` → `export` → commit `hasura/metadata/**`
- [ ] ADR trong `crm-backend/docs/adr/`

**Referral (`referral-backend`)**
- [ ] `app/graphql/Crm/ValidateReferralOrder.graphql` (một variable duy nhất)
- [ ] `app/src/Service/Crm/OrderValidationService.php`
- [ ] Tách address-tuple builder khỏi `PromotionService::resolveClientCompany()` để dùng chung
- [ ] `ReferralOrderService::queryOrderValidation()` (seam) + `assertOrderValidByCrm()`
- [ ] Call site sau `classifyProductLines` (`:313`), gate `$termedProductIds !== []`
- [ ] Warning về FE ở preview (khuôn `attachPromotionAnswer`)
- [ ] Test seam trong `TestableReferralOrderService` + 8 case ở §8.2
- [ ] `composer dump-autoload`
- [ ] ADR mới trong `docs/adr/` **hoặc** amend ADR-0017 §Considered Options
- [ ] `vendor/bin/ecs check` (file trong `src/GraphQL/` — ngoài đó tự sắp import)
- [ ] `php bin/console lint:container --resolve-env-vars` exit 0
- [ ] `python3 scripts/extract_resolvers.py > docs/api/resolvers-catalog.md` nếu có resolver đổi
      (task này không thêm resolver referral nên có thể không cần — verify bằng
      `bash scripts/check-generated-docs.sh`)

**Deploy**
- [ ] CRM deploy + `hasura:metadata:apply`
- [ ] Reload remote schema `crm` ở Hasura referral
- [ ] Verify field tồn tại
- [ ] Referral deploy

**Ticket riêng (§6)**
- [ ] `OrderRequestHandler` nuốt exception khi sync fail sau paid
- [ ] `isSetEndMonth` lệch giữa CRM và `computeEndDate()`
