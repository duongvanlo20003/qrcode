"""
QR Code Scanner — Production Deploy (Windows + Webcam)
======================================================
Tính năng:
  ✅ Ghi log toàn bộ QR quét được (CSV)
  ✅ Ghi log QR thất bại kèm hình ảnh crop (PNG)
  ✅ Thống kê realtime: count, duplicate, failed
  ✅ Cấu hình tốc độ băng chuyền tối đa
  ✅ Panel live trên màn hình
  ✅ Logic chống jitter: QR phải vắng N frame liên tiếp mới coi là "rời frame"
  ✅ Xuất CSV kho hàng bất kỳ lúc nào (phím E) hoặc tự động khi thoát

Phím tắt:
  Q → Thoát + tự động xuất warehouse CSV + báo cáo JSON
  E → Xuất warehouse CSV ngay lập tức (không thoát)
  S → Chụp màn hình
  R → Reset session
  P → Pause / Resume
"""

import cv2
import numpy as np
import time
import os
import sys
import csv
import json
from datetime import datetime
from pathlib import Path
from collections import deque

# ── Kiểm tra thư viện ────────────────────────────────────────────────────────
try:
    from ultralytics import YOLO
except ImportError:
    print("❌ Chưa cài ultralytics. Chạy: pip install ultralytics"); sys.exit(1)
try:
    from pyzbar import pyzbar
    PYZBAR_OK = True
except ImportError:
    PYZBAR_OK = False
try:
    import zxingcpp
    from PIL import Image
    ZXING_OK = True
except ImportError:
    ZXING_OK = False

# ════════════════════════════════════════════════════════════════════════════
# ██  CONFIG — Chỉnh tất cả tham số tại đây
# ════════════════════════════════════════════════════════════════════════════
CONFIG = {
    # ── Model & Camera ───────────────────────────────────────────────────────
    "MODEL_PATH":      "best.pt",
    "CAMERA_INDEX":    0,
    "FRAME_W":         1280,
    "FRAME_H":         720,

    # ── Detection ────────────────────────────────────────────────────────────
    "CONF_THRESH":     0.45,   # Ngưỡng YOLO detect (0.0–1.0)
    "MIN_CONF_ACCEPT": 0.50,   # Ngưỡng tối thiểu để accept nội dung QR
    "IOU_NMS":         0.40,   # IoU để loại box trùng trong 1 frame

    # ── TỐC ĐỘ BĂNG CHUYỀN ──────────────────────────────────────────────────
    #
    # Công thức tính tốc độ tối đa:
    #   max_speed (cm/s) = (QR_size_cm × Camera_FPS) / MIN_FRAMES_VISIBLE
    #
    # Ví dụ: QR 3cm, FPS=30, MIN_FRAMES=3
    #   → max = (3 × 30) / 3 = 30 cm/s ≈ 18 m/phút
    #
    # Chọn preset phù hợp với băng chuyền thực tế:
    #
    #  Preset  | MIN_FRAMES | Tốc độ tối đa (QR 3cm, 30 FPS)
    #  slow    |     5      | ~18 cm/s  (~11 m/phút)  — băng chuyền chậm
    #  medium  |     3      | ~30 cm/s  (~18 m/phút)  — mặc định
    #  fast    |     2      | ~45 cm/s  (~27 m/phút)  — băng chuyền nhanh
    #  custom  |  (CUSTOM_MIN_FRAMES bên dưới)
    #
    # Để tăng tốc độ tối đa hơn nữa:
    #   1. Dùng camera 60FPS trở lên
    #   2. Giảm MIN_FRAMES về 1 (rủi ro bỏ sót nhiều hơn)
    #   3. Đặt camera gần băng chuyền hơn (QR to hơn trong frame)
    "CONVEYOR_SPEED_PRESET": "medium",  # "slow" | "medium" | "fast" | "custom"
    "CUSTOM_MIN_FRAMES":  3,    # Chỉ dùng khi preset = "custom"
    "CUSTOM_QR_SIZE_CM":  3.0,  # Kích thước thực tế QR trên hàng (cm)
    "CAMERA_FPS":        30,    # FPS thực tế của camera

    # ── Decode ───────────────────────────────────────────────────────────────
    "DECODE_EVERY":    3,       # Decode mỗi N frame (tăng → FPS cao hơn)
    "MIN_CONTENT_LEN": 4,       # Độ dài tối thiểu content hợp lệ

    # ── Logging ──────────────────────────────────────────────────────────────
    "SESSION_DIR":      "sessions",  # Thư mục lưu tất cả session
    "SAVE_FAILED_IMG":  True,        # Lưu ảnh crop của QR thất bại
    "FAILED_IMG_LIMIT": 200,         # Giới hạn số ảnh failed lưu

    # ── Chống jitter / re-entry ──────────────────────────────────────────────
    # Số frame liên tiếp QR KHÔNG xuất hiện → mới coi là "đã rời frame"
    # Tăng lên nếu camera bị rung, giảm nếu băng chuyền quá nhanh
    "ABSENT_FRAMES_BEFORE_RESET": 8,
}

# ── Tính tốc độ tối đa từ preset ────────────────────────────────────────────
_PRESET_FRAMES = {"slow": 5, "medium": 3, "fast": 2,
                  "custom": CONFIG["CUSTOM_MIN_FRAMES"]}
MIN_FRAMES_VISIBLE = _PRESET_FRAMES.get(CONFIG["CONVEYOR_SPEED_PRESET"], 3)
MAX_SPEED_CMS = (CONFIG["CUSTOM_QR_SIZE_CM"] * CONFIG["CAMERA_FPS"]) / MIN_FRAMES_VISIBLE
MAX_SPEED_MPM = MAX_SPEED_CMS * 0.6

# ════════════════════════════════════════════════════════════════════════════
# SESSION SETUP
# ════════════════════════════════════════════════════════════════════════════
SESSION_ID   = datetime.now().strftime("%Y%m%d_%H%M%S")
SESSION_PATH = Path(CONFIG["SESSION_DIR"]) / SESSION_ID
FAILED_PATH  = SESSION_PATH / "failed_images"
SESSION_PATH.mkdir(parents=True, exist_ok=True)
FAILED_PATH.mkdir(parents=True, exist_ok=True)

CSV_OK   = SESSION_PATH / "qr_success.csv"
CSV_FAIL = SESSION_PATH / "qr_failed.csv"
JSON_SUM = SESSION_PATH / "summary.json"

def init_csv():
    with open(CSV_OK, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            "id","timestamp","content","confidence","decode_method",
            "bbox","bbox_size_px","is_duplicate","dup_count","frame_id"])
    with open(CSV_FAIL, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            "id","timestamp","confidence","fail_reason",
            "bbox","bbox_size_px","failed_image_path","frame_id"])

def log_success(rid, content, conf, method, bbox, frame_id, is_dup, dup_count):
    bw,bh = bbox[2]-bbox[0], bbox[3]-bbox[1]
    with open(CSV_OK,"a",newline="",encoding="utf-8") as f:
        csv.writer(f).writerow([
            rid, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            content, f"{conf:.3f}", method,
            f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
            f"{bw}x{bh}", is_dup, dup_count, frame_id])

def log_failed(rid, conf, reason, bbox, frame_id, failed_img=""):
    bw,bh = bbox[2]-bbox[0], bbox[3]-bbox[1]
    with open(CSV_FAIL,"a",newline="",encoding="utf-8") as f:
        csv.writer(f).writerow([
            rid, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            f"{conf:.3f}", reason,
            f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
            f"{bw}x{bh}", failed_img, frame_id])

init_csv()

# ════════════════════════════════════════════════════════════════════════════
# DECODE ENGINE
# ════════════════════════════════════════════════════════════════════════════
def try_decode(img):
    gray = img if len(img.shape)==2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bgr  = cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR) if len(img.shape)==2 else img
    if ZXING_OK:
        res = zxingcpp.read_barcodes(Image.fromarray(gray))
        if res: return res[0].text
    if PYZBAR_OK:
        d = pyzbar.decode(bgr)
        if d: return d[0].data.decode("utf-8",errors="replace")
        d = pyzbar.decode(gray)
        if d: return d[0].data.decode("utf-8",errors="replace")
    return None

def smart_decode(frame_bgr, x1,y1,x2,y2, padding=15):
    H,W = frame_bgr.shape[:2]
    crop = frame_bgr[max(0,y1-padding):min(H,y2+padding),
                     max(0,x1-padding):min(W,x2+padding)]
    if crop.size==0 or min(crop.shape[:2])<10:
        return None,"invalid_crop",crop
    r = try_decode(crop)
    if r: return r,"direct",crop

    rs   = cv2.resize(crop,(300,300),interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(rs,cv2.COLOR_BGR2GRAY)
    cl   = cv2.createCLAHE(clipLimit=3.0,tileGridSize=(8,8))
    variants = [
        ("resized",    rs),
        ("clahe",      cl.apply(gray)),
        ("otsu",       cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]),
        ("sharpen",    np.clip(cv2.filter2D(gray,-1,np.array([[0,-1,0],[-1,5,-1],[0,-1,0]],np.float32)),0,255).astype(np.uint8)),
        ("denoise",    cl.apply(cv2.fastNlMeansDenoising(gray,h=10))),
        ("adaptive",   cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,11,2)),
        ("clahe_otsu", cv2.threshold(cl.apply(gray),0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]),
    ]
    for name,v in variants:
        r = try_decode(v)
        if r: return r,name,crop
    return None,"all_failed",crop

def validate(content, conf):
    if not content or len(content.strip())<CONFIG["MIN_CONTENT_LEN"]:
        return False,"content_too_short"
    if sum(c.isprintable() for c in content)/len(content)<0.85:
        return False,"garbage_chars"
    if conf<CONFIG["MIN_CONF_ACCEPT"]:
        return False,f"low_conf_{conf:.2f}"
    return True,"ok"

def iou(b1,b2):
    ix1,iy1=max(b1[0],b2[0]),max(b1[1],b2[1])
    ix2,iy2=min(b1[2],b2[2]),min(b1[3],b2[3])
    inter=max(0,ix2-ix1)*max(0,iy2-iy1)
    if inter==0: return 0.0
    return inter/((b1[2]-b1[0])*(b1[3]-b1[1])+(b2[2]-b2[0])*(b2[3]-b2[1])-inter)

# ════════════════════════════════════════════════════════════════════════════
# STATS
# ════════════════════════════════════════════════════════════════════════════
class Stats:
    def __init__(self):
        self.unique_qr=0; self.duplicates=0
        self.failed_decode=0; self.invalid_content=0
        self.failed_img_count=0; self.log_id=0
        self.session_seen={}    # {content: {count,first_time,last_time,conf}}
        self.active_in_frame=set()      # QR đang trong frame hiện tại
        self._absent_counter={}         # {content: số frame liên tiếp không thấy}

    def _id(self):
        self.log_id+=1; return self.log_id

    def update_active(self, visible_contents):
        """Gọi mỗi frame với tập QR đang nhìn thấy.
        Dùng bộ đếm absent để chống jitter: chỉ xóa active sau N frame vắng.
        """
        threshold = CONFIG["ABSENT_FRAMES_BEFORE_RESET"]
        # Tăng bộ đếm cho QR không thấy trong frame này
        for c in list(self._absent_counter):
            if c not in visible_contents:
                self._absent_counter[c] = self._absent_counter.get(c,0) + 1
                if self._absent_counter[c] >= threshold:
                    self.active_in_frame.discard(c)
                    del self._absent_counter[c]
            else:
                self._absent_counter[c] = 0   # thấy lại → reset counter
        # QR mới xuất hiện: thêm vào absent_counter để theo dõi
        for c in visible_contents:
            if c not in self._absent_counter:
                self._absent_counter[c] = 0

    def ok(self, content, conf, method, bbox, frame_id):
        rid=self._id()
        # Duplicate chỉ khi QR vẫn đang được coi là "active" (chưa rời frame đủ lâu)
        already_seen   = content in self.session_seen
        still_in_frame = content in self.active_in_frame
        is_dup = already_seen and still_in_frame

        if is_dup:
            self.duplicates+=1
            self.session_seen[content]["count"]+=1
            self.session_seen[content]["last_time"]=datetime.now().strftime("%H:%M:%S")
            dup_count=self.session_seen[content]["count"]
        else:
            # Lần đầu HOẶC QR đã rời frame đủ lâu rồi quay lại → đếm thêm 1
            if not already_seen:
                self.unique_qr+=1
                self.session_seen[content]={"count":1,
                    "first_time":datetime.now().strftime("%H:%M:%S"),
                    "last_time": datetime.now().strftime("%H:%M:%S"),
                    "conf":conf}
            else:
                self.session_seen[content]["count"]+=1
                self.session_seen[content]["last_time"]=datetime.now().strftime("%H:%M:%S")
            # Đánh dấu active ngay sau lần quét thành công
            self.active_in_frame.add(content)
            dup_count=self.session_seen[content]["count"]
        log_success(rid,content,conf,method,bbox,frame_id,is_dup,dup_count)
        return is_dup,dup_count

    def fail(self, conf, reason, bbox, frame_id, frame_bgr, crop):
        self.failed_decode+=1; rid=self._id()
        img_path=""
        if CONFIG["SAVE_FAILED_IMG"] and self.failed_img_count<CONFIG["FAILED_IMG_LIMIT"]:
            fname=f"failed_{rid:05d}_{reason[:18]}.jpg"
            img_path=str(FAILED_PATH/fname)
            save=crop if (crop is not None and crop.size>0) else frame_bgr
            cv2.imwrite(img_path,save)
            self.failed_img_count+=1
        log_failed(rid,conf,reason,bbox,frame_id,img_path)

    def invalid(self, conf, reason, bbox, frame_id):
        self.invalid_content+=1
        log_failed(self._id(),conf,f"invalid:{reason}",bbox,frame_id)

stats = Stats()

def export_warehouse_csv():
    """Xuất CSV tổng hợp kho hàng: mỗi QR unique 1 dòng, kèm số lần quét."""
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SESSION_PATH / f"warehouse_{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["no","content","scan_count","first_seen","last_seen"])
        for i,(content,meta) in enumerate(stats.session_seen.items(), 1):
            w.writerow([i, content, meta["count"],
                        meta["first_time"], meta["last_time"]])
    print(f"📦 Xuất kho hàng → {path.name}  ({len(stats.session_seen)} mặt hàng)")
    return path


# ════════════════════════════════════════════════════════════════════════════
# DRAW
# ════════════════════════════════════════════════════════════════════════════
C_OK=(50,220,50); C_DUP=(0,200,220); C_FAIL=(0,150,255); C_INV=(80,80,200)

def draw_box(frame,x1,y1,x2,y2,label,color):
    cl=min(18,(x2-x1)//4,(y2-y1)//4); t=2
    for px,py,dx,dy in [(x1,y1,1,1),(x2,y1,-1,1),(x1,y2,1,-1),(x2,y2,-1,-1)]:
        cv2.line(frame,(px,py),(px+dx*cl,py),color,t+1)
        cv2.line(frame,(px,py),(px,py+dy*cl),color,t+1)
    fnt=cv2.FONT_HERSHEY_SIMPLEX; fs=0.48
    (tw,th),_=cv2.getTextSize(label,fnt,fs,1)
    lx,ly=x1,max(y1-5,th+4)
    cv2.rectangle(frame,(lx,ly-th-4),(lx+tw+6,ly+2),color,-1)
    cv2.putText(frame,label,(lx+3,ly-2),fnt,fs,(15,15,15),1,cv2.LINE_AA)

def draw_panel(frame, fps, paused, frame_count, elapsed_s):
    H,W=frame.shape[:2]; pw=340
    p=np.zeros((H,pw,3),np.uint8); p[:]=22
    fnt=cv2.FONT_HERSHEY_SIMPLEX; y=20

    cv2.putText(p,"QR SCANNER  v3.0",(8,y),fnt,0.6,(200,200,200),1); y+=24
    st="PAUSED" if paused else f"LIVE  {fps:.1f} FPS"
    cv2.putText(p,st,(8,y),fnt,0.5,(0,180,255) if paused else (50,220,50),1); y+=18
    es=f"{int(elapsed_s//60):02d}:{int(elapsed_s%60):02d}"
    cv2.putText(p,f"Session {es}  |  #{frame_count}",(8,y),fnt,0.38,(100,100,100),1); y+=16

    preset=CONFIG["CONVEYOR_SPEED_PRESET"].upper()
    cv2.putText(p,f"Conveyor: {preset}  {MAX_SPEED_CMS:.0f}cm/s ({MAX_SPEED_MPM:.0f}m/min)",
                (8,y),fnt,0.38,(120,180,255),1); y+=16
    cv2.line(p,(6,y),(pw-6,y),(55,55,55),1); y+=12

    def row(lbl,val,col):
        nonlocal y
        cv2.putText(p,lbl,(8,y),fnt,0.42,(150,150,150),1)
        cv2.putText(p,str(val),(pw-60,y),fnt,0.5,col,1); y+=17

    cv2.putText(p,"STATISTICS",(8,y),fnt,0.48,(180,180,180),1); y+=18
    row("Unique QR scanned",  stats.unique_qr,        C_OK)
    row("Duplicate hits",     stats.duplicates,       C_DUP)
    row("Failed decode",      stats.failed_decode,    C_FAIL)
    row("Invalid content",    stats.invalid_content,  C_INV)
    row("Failed imgs saved",  stats.failed_img_count, (110,110,110))
    cv2.line(p,(6,y),(pw-6,y),(55,55,55),1); y+=12

    cv2.putText(p,f"RECENT QR ({len(stats.session_seen)})",(8,y),fnt,0.46,(180,180,180),1); y+=18
    for content,meta in list(reversed(list(stats.session_seen.items())))[:11]:
        cv2.putText(p,f"x{meta['count']}",(8,y),fnt,0.4,(80,220,80),1)
        disp=(content[:29]+"…") if len(content)>29 else content
        cv2.putText(p,disp,(42,y),fnt,0.36,(200,200,200),1); y+=14
        cv2.putText(p,f"  {meta['first_time']} → {meta['last_time']}",(8,y),fnt,0.33,(80,80,80),1); y+=14
        if y>H-70: break

    cv2.line(p,(6,H-52),(pw-6,H-52),(55,55,55),1)
    cv2.putText(p,"[Q]Quit [S]Shot [E]Export CSV [R]Reset [P]Pause",(8,H-36),fnt,0.33,(100,100,100),1)
    cv2.putText(p,f"Log: {SESSION_PATH.name}",(8,H-18),fnt,0.33,(70,70,70),1)
    return np.hstack([frame,p])

# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════
def main():
    print(f"\n{'='*55}")
    print(f"  QR Scanner — Session {SESSION_ID}")
    print(f"{'='*55}")
    print(f"  Conveyor preset : {CONFIG['CONVEYOR_SPEED_PRESET'].upper()}")
    print(f"  Max speed       : {MAX_SPEED_CMS:.1f} cm/s  ({MAX_SPEED_MPM:.1f} m/min)")
    print(f"  Min frames vis. : {MIN_FRAMES_VISIBLE} frames @ {CONFIG['CAMERA_FPS']} FPS")
    print(f"  QR size (real)  : {CONFIG['CUSTOM_QR_SIZE_CM']} cm")
    print(f"  YOLO conf       : {CONFIG['CONF_THRESH']}")
    print(f"  Accept conf min : {CONFIG['MIN_CONF_ACCEPT']}")
    print(f"  Decode every    : {CONFIG['DECODE_EVERY']} frames")
    print(f"  Session folder  : {SESSION_PATH}")
    print(f"{'='*55}\n")

    if not Path(CONFIG["MODEL_PATH"]).exists():
        print(f"❌ Không tìm thấy {CONFIG['MODEL_PATH']}"); sys.exit(1)

    print("🔄 Loading model...")
    model=YOLO(CONFIG["MODEL_PATH"]); print("✅ Model OK")

    cap=cv2.VideoCapture(CONFIG["CAMERA_INDEX"],cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"❌ Không mở được camera {CONFIG['CAMERA_INDEX']}"); sys.exit(1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CONFIG["FRAME_W"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CONFIG["FRAME_H"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE,1)
    print(f"✅ Camera: {int(cap.get(3))}×{int(cap.get(4))}\n")

    fps_buf=deque(maxlen=30); t_prev=t_start=time.time()
    frame_count=0; paused=False; last_results=[]; scr_n=0

    cv2.namedWindow("QR Scanner",cv2.WINDOW_NORMAL)
    cv2.resizeWindow("QR Scanner",CONFIG["FRAME_W"]+340,CONFIG["FRAME_H"])

    while True:
        ret,frame=cap.read()
        if not ret: print("⚠️  Mất camera"); break
        frame_count+=1
        t_now=time.time()
        fps_buf.append(1.0/max(t_now-t_prev,1e-6)); t_prev=t_now
        fps=sum(fps_buf)/len(fps_buf); elapsed=t_now-t_start

        if paused:
            cv2.imshow("QR Scanner",draw_panel(frame.copy(),fps,True,frame_count,elapsed))
            k=cv2.waitKey(30)&0xFF
            if k==ord('q'): break
            if k==ord('p'): paused=False
            continue

        # ── YOLO detect ──────────────────────────────────────────────────────
        yolo_res=model(frame,conf=CONFIG["CONF_THRESH"],verbose=False)[0]
        do_decode=(frame_count%CONFIG["DECODE_EVERY"]==0)
        current=[]

        if len(yolo_res.boxes)>0:
            blist=[(int(b.xyxy[0][0]),int(b.xyxy[0][1]),
                    int(b.xyxy[0][2]),int(b.xyxy[0][3]),
                    float(b.conf[0])) for b in yolo_res.boxes]
            blist.sort(key=lambda x:-x[4])
            keep=[]; sup=set()
            for i,bi in enumerate(blist):
                if i in sup: continue
                keep.append(i)
                for j,bj in enumerate(blist):
                    if j!=i and j not in sup:
                        if iou(bi[:4],bj[:4])>CONFIG["IOU_NMS"]: sup.add(j)

            for i in keep:
                x1,y1,x2,y2,conf=blist[i]
                content=None; method="skip"; status="detecting"

                if do_decode:
                    content,method,crop=smart_decode(frame,x1,y1,x2,y2)
                    if content:
                        ok,reason=validate(content,conf)
                        if ok:
                            # ── Chỉ ghi nhận nếu QR này CHƯA active (chưa được đếm lần này) ──
                            if content not in stats.active_in_frame:
                                is_dup,dup_c=stats.ok(content,conf,method,(x1,y1,x2,y2),frame_count)
                                status="duplicate" if is_dup else "ok"
                            else:
                                # QR đã được đếm rồi, chỉ hiển thị lại, không gọi stats.ok()
                                status="duplicate"
                        else:
                            stats.invalid(conf,reason,(x1,y1,x2,y2),frame_count)
                            status="invalid"; content=None
                    else:
                        stats.fail(conf,method,(x1,y1,x2,y2),frame_count,frame,crop)
                        status="failed"
                else:
                    # Frame không decode: kế thừa kết quả frame trước
                    for prev in last_results:
                        if iou((x1,y1,x2,y2),prev["bbox"])>0.5:
                            content=prev.get("content"); status=prev.get("status","detecting"); break

                current.append({"bbox":(x1,y1,x2,y2),"conf":conf,
                                 "content":content,"status":status})
            last_results=current

        # Cập nhật active LUÔN LUÔN (kể cả frame không có box nào)
        visible = {r["content"] for r in current if r.get("content") and r.get("status") in ("ok","duplicate")}
        stats.update_active(visible)

        # ── Draw ─────────────────────────────────────────────────────────────
        df=frame.copy()
        for r in current:
            x1,y1,x2,y2=r["bbox"]; conf=r["conf"]
            c=r["content"]; s=r["status"]
            if   s=="ok":
                col=C_OK;   lbl=f"{conf:.2f} NEW | {(c or '')[:22]}"
            elif s=="duplicate":
                cnt=stats.session_seen.get(c,{}).get("count",1)
                col=C_DUP;  lbl=f"{conf:.2f} DUP x{cnt} | {(c or '')[:18]}"
            elif s=="invalid":
                col=C_INV;  lbl=f"{conf:.2f} INVALID"
            else:
                col=C_FAIL; lbl=f"{conf:.2f} scanning…"
            draw_box(df,x1,y1,x2,y2,lbl,col)
            if c and s in ("ok","duplicate"):
                txt=(c[:55]+"…") if len(c)>55 else c
                cv2.putText(df,txt,(x1,min(y2+16,df.shape[0]-4)),
                            cv2.FONT_HERSHEY_SIMPLEX,0.42,col,1,cv2.LINE_AA)

        cv2.imshow("QR Scanner",draw_panel(df,fps,False,frame_count,elapsed))

        k=cv2.waitKey(1)&0xFF
        if   k==ord('q'): break
        elif k==ord('s'):
            scr_n+=1; p=SESSION_PATH/f"screenshot_{scr_n:03d}.png"
            cv2.imwrite(str(p),draw_panel(df,fps,False,frame_count,elapsed))
            print(f"📸 {p.name}")
        elif k==ord('e'):
            if stats.session_seen:
                export_warehouse_csv()
            else:
                print("⚠️  Chưa có QR nào được quét")
        elif k==ord('r'):
            stats.session_seen.clear(); stats.active_in_frame.clear()
            stats._absent_counter.clear(); last_results=[]
            print("🔄 Reset session")
        elif k==ord('p'):
            paused=True; print("⏸  Paused")

    cap.release(); cv2.destroyAllWindows()

    # ── Tự động xuất warehouse CSV khi thoát ────────────────────────────────
    if stats.session_seen:
        export_warehouse_csv()

    # ── Xuất báo cáo ─────────────────────────────────────────────────────────
    elapsed_total=time.time()-t_start
    summary={
        "session_id":      SESSION_ID,
        "duration_sec":    round(elapsed_total,1),
        "total_frames":    frame_count,
        "avg_fps":         round(frame_count/max(elapsed_total,1),1),
        "conveyor": {
            "preset":       CONFIG["CONVEYOR_SPEED_PRESET"],
            "max_speed_cms":round(MAX_SPEED_CMS,1),
            "max_speed_mpm":round(MAX_SPEED_MPM,1),
            "min_frames_visible": MIN_FRAMES_VISIBLE,
            "qr_size_cm":   CONFIG["CUSTOM_QR_SIZE_CM"],
            "camera_fps":   CONFIG["CAMERA_FPS"],
        },
        "stats":{
            "unique_qr_scanned": stats.unique_qr,
            "duplicate_hits":    stats.duplicates,
            "failed_decode":     stats.failed_decode,
            "invalid_content":   stats.invalid_content,
            "failed_imgs_saved": stats.failed_img_count,
        },
        "qr_list":[
            {"content":c,"scan_count":m["count"],
             "first_seen":m["first_time"],"last_seen":m["last_time"]}
            for c,m in stats.session_seen.items()
        ]
    }
    with open(JSON_SUM,"w",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)

    print(f"\n{'='*55}")
    print(f"  SESSION SUMMARY — {SESSION_ID}")
    print(f"{'='*55}")
    print(f"  Duration        : {int(elapsed_total//60):02d}:{int(elapsed_total%60):02d}")
    print(f"  Avg FPS         : {summary['avg_fps']}")
    print(f"  Unique QR       : {stats.unique_qr}")
    print(f"  Duplicates      : {stats.duplicates}")
    print(f"  Failed decode   : {stats.failed_decode}")
    print(f"  Invalid content : {stats.invalid_content}")
    print(f"  Max speed       : {MAX_SPEED_CMS:.0f} cm/s  ({MAX_SPEED_MPM:.0f} m/min)")
    print(f"{'='*55}")
    print(f"\n📂 Log → {SESSION_PATH}")
    print(f"   ✅ {CSV_OK.name}   — QR thành công")
    print(f"   ❌ {CSV_FAIL.name}  — QR thất bại")
    print(f"   📊 {JSON_SUM.name} — Tóm tắt")
    if stats.failed_img_count>0:
        print(f"   🖼  failed_images/  — {stats.failed_img_count} ảnh crop thất bại")

if __name__=="__main__":
    main()