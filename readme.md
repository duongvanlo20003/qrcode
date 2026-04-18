# QR Scanner — Hướng dẫn Deploy Windows

## Cấu trúc thư mục
```
qr_deploy_windows/
├── qr_scanner.py      ← Code chính
├── requirements.txt   ← Thư viện cần cài
├── run.bat            ← Click đúp để chạy
└── output/            ← Ảnh chụp màn hình (tự tạo)
```

## Bước 1 — Lấy model từ Kaggle
Sau khi train xong trên Kaggle, download file:
```
/kaggle/working/runs/qr_aug_30ep/weights/best.pt
```
→ Đặt vào cùng thư mục với `qr_scanner.py`

## Bước 2 — Cài Python (nếu chưa có)
- Tải tại: https://www.python.org/downloads/
- **Tick vào "Add Python to PATH"** khi cài

## Bước 3 — Chạy
Double-click `run.bat` — script tự cài thư viện và mở camera.

Hoặc chạy thủ công:
```bash
pip install -r requirements.txt
python qr_scanner.py
```

## Phím tắt khi chạy
| Phím | Chức năng |
|------|-----------|
| Q    | Thoát     |
| S    | Chụp màn hình lưu vào output/ |
| R    | Reset session (xoá danh sách QR đã scan) |
| P    | Pause / Resume |

## Màu box trên màn hình
| Màu | Ý nghĩa |
|-----|---------|
| 🟢 Xanh lá | Decode thành công — QR mới |
| 🟡 Vàng    | QR đã scan rồi (duplicate) |
| 🟠 Cam     | Detect được nhưng chưa decode |

## Tuỳ chỉnh trong qr_scanner.py
```python
CAMERA_INDEX = 0      # Đổi sang 1, 2... nếu có nhiều webcam
CONF_THRESH  = 0.45   # Giảm nếu bỏ sót QR, tăng nếu detect nhầm
MIN_CONF_DB  = 0.50   # Ngưỡng để accept QR (tránh đọc sai)
FRAME_W      = 1280   # Độ phân giải camera
FRAME_H      = 720
DECODE_EVERY = 3      # Decode mỗi 3 frame — tăng FPS
```

## Troubleshooting
| Lỗi | Cách xử lý |
|-----|------------|
| Không mở được camera | Đổi `CAMERA_INDEX = 1` |
| FPS thấp | Tăng `DECODE_EVERY = 5` hoặc `CONF_THRESH = 0.6` |
| Bỏ sót QR | Giảm `CONF_THRESH = 0.3` |
| Đọc sai content | Tăng `MIN_CONF_DB = 0.65` |
| pyzbar lỗi | Cài Visual C++ Redistributable từ Microsoft |