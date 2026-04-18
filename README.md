# 🏭 Industrial QR Detection System

> **Real-time QR code detection & factory yield monitoring** trên băng chuyền sản xuất, sử dụng Computer Vision (YOLOv8 + pyzbar), WebSocket streaming, và dashboard React được containerize hoàn toàn bằng Docker.

---

## 📸 Demo

| Trạng thái | Mô tả |
|---|---|
| 🟡 **STANDBY** | Chưa có phiên scan — backend sẵn sàng |
| 🟢 **SYSTEM LIVE** | Phiên đang chạy, WebSocket kết nối, camera streaming |
| 🔴 **OFFLINE** | Có phiên nhưng mất kết nối WebSocket |

---

## 🗂️ Cấu trúc dự án

```
qrcode/
├── backend/
│   ├── main.py              # FastAPI app, WebSocket, tracking logic
│   ├── qr_logic.py          # Smart decode pipeline + IOU utility
│   ├── requirements.txt
│   ├── Dockerfile
│   └── best.pt              # ← Custom YOLOv8 model (cần cung cấp)
├── frontend/
│   ├── src/
│   │   └── App.tsx          # React UI — camera, metrics, session management
│   ├── Dockerfile
│   └── package.json
├── mysql_init/
│   └── init.sql             # Schema khởi tạo tự động
├── docker-compose.yml
├── run_dev.sh               # Script quản lý vòng đời hệ thống
└── gen_conveyor_data.py     # Script sinh QR data mẫu để test
```

---

## ⚙️ Tech Stack

| Layer | Technology | Mục đích |
|---|---|---|
| **AI / CV** | YOLOv8n (Ultralytics) | Object detection — locate QR codes |
| **AI / CV** | OpenCV KCF Tracker | Smooth bounding box giữa các YOLO frame |
| **Decode** | pyzbar (libzbar) | Decode QR/DataMatrix từ crop vùng detect |
| **Backend** | FastAPI + Uvicorn | REST API + async WebSocket server |
| **Concurrency** | `ThreadPoolExecutor(4)` | YOLO và tracker chạy song song không block event loop |
| **Database** | MySQL 8 + SQLAlchemy | Lưu scan results và session history |
| **Frontend** | React + TypeScript + Vite | Dashboard real-time |
| **Styling** | Tailwind CSS | Dark-mode industrial UI |
| **Infra** | Docker + Docker Compose | Multi-container orchestration |
| **Proxy** | Nginx (Alpine) | Serve static frontend build |
| **Admin DB** | Adminer | Quản lý MySQL qua browser |

---

## 🚀 Setup & Chạy

### Yêu cầu

- Docker Desktop (Mac/Windows/Linux)
- File `backend/best.pt` — custom YOLOv8 model (xem phần Training bên dưới)

### Lần đầu

```bash
# Clone repo
git clone <repo-url>
cd qrcode

# Cấp quyền script
chmod +x run_dev.sh

# Khởi động toàn bộ hệ thống
./run_dev.sh start
```

### Các lệnh vận hành

```bash
./run_dev.sh start    # Khởi động lần đầu (build + run)
./run_dev.sh restart  # Build lại và restart (dùng khi thay đổi code)
./run_dev.sh stop     # Dừng tất cả container
./run_dev.sh logs     # Xem log realtime
```

### Truy cập

| Service | URL |
|---|---|
| Frontend Dashboard | http://localhost:3000 |
| Backend API | http://localhost:8000/docs |
| MySQL Adminer | http://localhost:8080 |

> Adminer: Server = `mysqldb`, User = `root`, Pass = `root`, DB = `qr_scanner`

---

## 🤖 Build Model `best.pt` (YOLOv8 Custom)

Model `best.pt` được train để detect vùng QR code trên băng chuyền nhà máy — phân biệt với background phức tạp (ánh đèn công nghiệp, góc nghiêng, bề mặt hộp carton).

### 1. Chuẩn bị dataset

```
dataset/
├── images/
│   ├── train/   # ~80% ảnh
│   └── val/     # ~20% ảnh
├── labels/
│   ├── train/   # YOLO format: class cx cy w h (normalized)
│   └── val/
└── data.yaml
```

`data.yaml`:
```yaml
path: /kaggle/working/dataset
train: images/train
val:   images/val
nc: 1
names: ['qr_code']
```

### 2. Train trên Kaggle (GPU P100 miễn phí)

```python
from ultralytics import YOLO

model = YOLO('yolov8n.pt')  # start from nano pretrained

results = model.train(
    data='data.yaml',
    epochs=30,
    imgsz=640,
    batch=16,
    name='qr_aug_30ep',
    augment=True,       # mosaic, flipud, fliplr, hsv
    conf=0.45,
    device=0            # GPU
)
```

Sau khi train, file model nằm tại:
```
/kaggle/working/runs/qr_aug_30ep/weights/best.pt
```

Download và đặt vào `backend/best.pt`.

### 3. Augmentation áp dụng

| Kỹ thuật | Mục đích |
|---|---|
| Mosaic (4-image) | Dạy model nhận diện nhiều kích thước / mật độ |
| Random Flip | Invariant với hướng đặt hộp trên băng chuyền |
| HSV Jitter | Robust với ánh sáng nhà máy thay đổi |
| Random Scale | Detect QR từ xa và gần |
| Perspective warp | Xử lý camera góc nghiêng |

### 4. So sánh với fallback `yolov8n.pt`

| | `best.pt` (custom) | `yolov8n.pt` (generic) |
|---|---|---|
| **Accuracy QR** | ✅ Cao | ⚠️ Thấp (chưa train QR) |
| **False Positive** | ✅ Ít | ❌ Nhiều |
| **Confidence typical** | 0.75–0.95 | 0.45–0.60 |

Nếu `best.pt` không tồn tại, hệ thống tự fallback về `yolov8n.pt` với cảnh báo.

---

## 🧠 Kiến trúc xử lý ảnh (Image Processing Pipeline)

### Pipeline tổng quan

```
Camera Frame (JPEG base64 qua WebSocket)
    │
    ├──[YOLO Inference - mỗi 150ms]──────────────────────────────────────────►
    │   • YOLOv8 detect bounding box (conf ≥ 0.45)
    │   • NMS được thực hiện bởi YOLO (IoU > 0.4 → suppress)
    │   • Với mỗi box → smart_decode() → content
    │   • active_targets[] = RESET hoàn toàn mỗi cycle
    │
    └──[KCF Tracker - mỗi frame ~33ms]──────────────────────────────────────►
        • Mỗi target trong active_targets được track
        • Cập nhật bbox position smooth giữa YOLO frames
        • Nếu tracker fail liên tục (fc ≥ 5 frames) → xóa target
```

### `smart_decode()` — Cascade Fallback Strategy

Hàm decode không dừng ở bước đầu — nếu một phương pháp fail, nó thử phương pháp tiếp theo:

```
Input: cropped frame (từ YOLO bbox) + padding 30px

1. Direct decode        → pyzbar trên crop gốc (fastest, 0-5ms)
2. Multi-scale resize   → 400×400, 300×300, 500×500 (INTER_CUBIC)
3. Image enhancement (trên 400×400):
   ├── CLAHE           → contrast tốt hơn trong ánh sáng nhà máy
   ├── Otsu threshold  → binarize tối ưu tự động
   ├── Sharpen kernel  → làm rõ module QR bị mờ
   ├── Adaptive thresh → xử lý ánh sáng không đều
   ├── CLAHE + Otsu    → kết hợp
   ├── Inverted Otsu   → QR dark-on-light và light-on-dark
   └── Morph Close     → fill missing modules
4. Rotation fallback    → ±5°, ±10°, 90° (xử lý QR nghiêng)
5. Full-frame fallback  → đọc toàn bộ frame (nếu crop sai)
```

> ⚠️ **FastNlMeansDenoising được loại bỏ** — tốn 200–500ms/call, không đáng với QR code. Các biến thể khác tổng cộng < 20ms.

### Tại sao YOLO thay vì chỉ dùng pyzbar?

| | pyzbar only | YOLO + pyzbar |
|---|---|---|
| **QR nhỏ hoặc xa** | ❌ Miss | ✅ YOLO detect, zoom vào decode |
| **Nhiều QR cùng lúc** | ⚠️ Chậm | ✅ Parallel processing |
| **QR bị che 1 phần** | ❌ Fail | ✅ YOLO vẫn locate |
| **Ánh sáng kém** | ❌ Fail | ✅ Enhancement pipeline |
| **Tốc độ** | ~5ms | ~150ms/cycle nhưng smooth 60fps nhờ tracker |

---

## 🔄 Kiến trúc hệ thống Real-time

### WebSocket Frame Flow

```
Frontend (60fps capture)
    │ JPEG base64 string
    │ gửi qua WebSocket mỗi 33ms
    ▼
Backend WebSocket Handler
    ├── Nhận frame → decode base64 → OpenCV Mat
    ├── [Mỗi 150ms] Tạo asyncio.Task(_run_yolo)
    │       ├── ThreadPoolExecutor.submit(YOLO inference)
    │       ├── Với mỗi detection → smart_decode()
    │       ├── Ghi DB nếu status=ok + chưa thấy trong session
    │       └── active_targets[:] = results mới (REPLACE, không merge)
    ├── [Mỗi frame] Track active_targets với KCF
    │       └── Prune targets có fc ≥ 5 (tracker liên tục fail)
    └── Send response JSON về Frontend
            {detections, using_custom_model, timestamp, is_yolo}
```

### Tại sao reset `active_targets` thay vì merge?

Design cũ (buggy) merge target mới vào danh sách cũ — dẫn đến **bounding box tích lũy không xóa được**. Khi YOLO chạy xong, chỉ có N QR thực tế trước camera. Reset hoàn toàn đảm bảo hiển thị đúng N bounding boxes — không hơn không kém.

### Session Deduplication

Mỗi WebSocket connection duy trì một `session_seen: set()` in-memory:

```python
key = p_uuid or d["content"]
if key in session_seen:
    continue  # QR đã scan trong session này → bỏ qua
session_seen.add(key)
# → ghi DB
```

`p_uuid` là UUID trong QR content format `FACTORY|<uuid>|<type>|<qty>|<price>`. Dùng UUID làm key tránh đếm nhầm khi cùng hộp xuất hiện trong nhiều frame liên tiếp.

---

## 🗄️ Database Schema

### `scan_sessions`

| Column | Type | Mô tả |
|---|---|---|
| `id` | INT PK | Session ID |
| `name` | VARCHAR(100) | Tên tự động: "Session DD/MM/YYYY HH:MM:SS" |
| `start_time` | DATETIME | Thời điểm bắt đầu |
| `end_time` | DATETIME | Thời điểm kết thúc (null nếu còn active) |
| `is_active` | INT | 1 = đang chạy, 0 = đã kết thúc |

### `scan_results`

| Column | Type | Mô tả |
|---|---|---|
| `id` | INT PK | |
| `qr_content` | TEXT | Nội dung QR thô |
| `qr_uuid` | VARCHAR(50) | UUID trích từ QR content |
| `confidence` | FLOAT | YOLO confidence score |
| `method` | VARCHAR(50) | Decode method: direct/clahe/otsu/resize_400/... |
| `status` | VARCHAR(50) | ok / failed / invalid |
| `product_type` | VARCHAR(100) | Loại sản phẩm |
| `quantity` | INT | Số gói |
| `unit_price` | INT | Đơn giá (VNĐ) |
| `total_price` | INT | Tổng tiền = qty × unit_price |
| `image_base64` | MEDIUMTEXT | Ảnh crop của QR |
| `session_id` | INT FK | Liên kết với scan_sessions |

---

## 📡 REST API Endpoints

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/sessions` | Danh sách tất cả session + total_scans |
| `POST` | `/sessions` | Tạo session mới |
| `PUT` | `/sessions/{id}/end` | Kết thúc session |
| `DELETE` | `/sessions/{id}` | Xóa session + toàn bộ scan |
| `DELETE` | `/sessions/{id}/scans` | Xóa data scan của session (giữ session) |
| `GET` | `/scans?session_id={id}` | Lấy scan results của session |
| `WS` | `/ws/detect?session_id={id}` | Stream camera frames, nhận detections |

---

## 🎨 QR Data Format (Factory Protocol)

```
FACTORY|<UUID>|<ProductType>|<Quantity>|<UnitPrice>
```

Ví dụ:
```
FACTORY|a3b2f1e9-...|Bánh kem (Cake)|100|80000
```

Hệ thống parse và hiển thị:  
- **Loại**: Thạch (Jelly), Kẹo dẻo (Gummy), Kẹo xốp (Marshmallow), Bánh quy (Biscuit), Bánh kem (Cake)
- **Số lượng**: gói trong hộp
- **Đơn giá / Tổng tiền**: VNĐ

---

## 🛠️ Troubleshooting

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Warning: `best.pt not found` | Thiếu file model | Download từ Kaggle, đặt vào `backend/best.pt` |
| Build fail `TS6133` | Biến TS khai báo nhưng không dùng | Chạy `./run_dev.sh restart` sau khi đã fix code |
| Quá nhiều bounding box | active_targets tích lũy (bug cũ) | Đã fix: reset mỗi YOLO cycle |
| Total Scans tăng liên tục | Push detection mỗi frame thay vì mỗi event | Đã fix: chỉ push `status=ok` vào history |
| OFFLINE khi chưa có session | WebSocket chỉ active khi có session | Đã fix: hiển thị STANDBY thay vì OFFLINE |
| Frontend cache cũ | Docker layer cache | Chạy `./run_dev.sh restart` |

---

## 📝 License

MIT — for academic / industrial prototype use.
