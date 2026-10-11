# Mã lý do quyết định (reason_code)

File này là **nguồn chân lý** cho `reason_code` trả về từ `decide()`.
Bổ sung và thay thế phần mô tả mã lý do ở mục 5 và 11 của `SPS_design_v7.md`.

Mọi module (backend, firmware, web, bot) phải dùng đúng 8 mã dưới đây, không tự đặt mã mới.

## Bảng mã

| reason_code | Cổng áp dụng | OLED hiển thị | alert | Nội dung cảnh báo (Bot) |
|---|---|---|---|---|
| `ok` | cả hai | "Mời vào" | false | — |
| `card_unknown` | cả hai | "Thẻ không hợp lệ" | false | — (chỉ ghi log) |
| `card_locked` | cả hai | "Thẻ đã khóa" | true | "Thẻ của xe [biển số] vừa bị quẹt khi đang tạm khóa" + nút "Mở lại thẻ" (cho chủ thẻ) |
| `card_lost` | cả hai | "Thẻ đã khóa" | true | "Thẻ báo mất của xe [biển số] vừa bị quẹt" + ảnh + cảnh báo cho cả quản lý |
| `plan_expired` | chỉ cổng vào | "Thẻ hết hạn" | false | — |
| `already_in_lot` | chỉ cổng vào | "Xe đã ở trong bãi" | true | "Xe [biển số] đã ở trong bãi mà còn quẹt vào" + ảnh |
| `not_in_lot` | chỉ cổng ra | "Xe chưa vào bãi" | true | "Xe [biển số] chưa vào bãi mà quẹt ra" + ảnh |
| `lot_full` | chỉ cổng vào | "Hết chỗ" | false | — |

## Quy tắc quan trọng

1. **`card_locked` và `card_lost` cùng OLED message "Thẻ đã khóa"** — cố ý.
   Không hiển thị "Thẻ báo mất" trên OLED, vì người nhặt được thẻ sẽ biết đã bị phát hiện và có thể tẩu tán hoặc phá thẻ. Chỉ Bot mới phân biệt hai mã này.
2. **`plan_expired` chỉ áp dụng cổng vào.** Cổng ra không chặn vì lý do hết hạn gói (để xe không bị nhốt trong bãi).
3. **`alert = true`** nghĩa là sự kiện phải tạo bản ghi trong bảng `notifications` mức "cảnh báo", đẩy cho chủ thẻ và/hoặc quản lý, kèm ảnh nếu có.
4. **`alert = false`** nghĩa là chỉ ghi vào `events`, không đẩy thông báo.
5. **Mã không áp dụng cho cổng** (ví dụ `plan_expired` ở cổng ra): hàm `decide()` không bao giờ trả mã này. Nếu code nhận được, coi như lỗi logic, ghi log cảnh báo nội bộ.
6. **OLED cổng ra khi `ok` cũng hiện "Mời vào"** — giống cổng vào. Cố ý giữ đúng mục 11 design gốc, không phân biệt chữ hiển thị giữa hai cổng. Việc phân biệt vào/ra dựa vào trường `gate` trong sự kiện, không dựa vào OLED.

## Ánh xạ khi thêm khách vãng lai (tương lai)

Khi thêm loại chứng danh QR cho khách, dự kiến thêm các mã:
`guest_ok`, `guest_expired`, `guest_quota_full`. Chưa dùng ở phiên bản này.

## Mã sự kiện phụ (event-only codes)

Các mã dưới đây KHÔNG xuất hiện trong output của `decide()`. Chúng chỉ được ghi vào cột
`events.reason_code` khi hệ thống ghi nhận sự kiện không phải là quyết định quẹt thẻ:

| Mã | Khi nào dùng | Nguồn ghi |
|---|---|---|
| `no_entry` | Cư dân quẹt thẻ, cổng mở, hết 5 giây không có xe vào, cổng đóng lại | `events.record_no_entry` |
| `manual_adjust` | Quản lý sửa trạng thái `in_lot` thủ công (web hoặc API lõi) | `api_core.set_vehicle_in_lot` |

Quy tắc: khi thêm mã sự kiện phụ mới, phải cập nhật cả file này và `events.py` / `api_core.py`.

## Lịch sử thay đổi

- 2026-10-06: tạo file, chốt 9 mã cho phiên bản v7.
- 2026-10-06: bổ sung mục "Mã sự kiện phụ" cho các sự kiện không phải quyết định.
- 2026-10-08: gộp lot_full_car và lot_full_moto thành lot_full (bỏ phân biệt loại xe).