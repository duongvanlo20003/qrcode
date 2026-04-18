import os
import base64
import json
import asyncio
import logging
import cv2
import numpy as np
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("qr_backend")

# Thread pool: 4 workers — 1 for tracker (fast), 1 for YOLO (slow), 2 spare
_executor = ThreadPoolExecutor(max_workers=4)

def _create_tracker():
    """Create an OpenCV KCF tracker with compatibility for various versions."""
    if hasattr(cv2, 'TrackerKCF_create'):
        return cv2.TrackerKCF_create()
    try:
        if hasattr(cv2, 'TrackerKCF'):
            return cv2.TrackerKCF.create()
    except Exception:
        pass
    try:
        if hasattr(cv2, 'legacy') and hasattr(cv2.legacy, 'TrackerKCF_create'):
            return cv2.legacy.TrackerKCF_create()
    except Exception:
        pass
    raise AttributeError("OpenCV Tracking API not found. Please ensure 'opencv-contrib-python-headless' is installed.")

def _track_frame(tracker, frame):
    """Run one tracker step. Returns (success, (x, y, w, h))."""
    return tracker.update(frame)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.orm import declarative_base

from ultralytics import YOLO
from pyzbar.pyzbar import decode as pyzbar_decode
from qr_logic import smart_decode, iou

# Database setup
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "user")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_NAME = os.environ.get("DB_NAME", "qr_scanner")
DB_PORT = os.environ.get("DB_PORT", "3306")

SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ScanResult(Base):
    __tablename__ = "scan_results"
    id = Column(Integer, primary_key=True, index=True)
    qr_content = Column(Text, nullable=True) 
    confidence = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    method = Column(String(50), nullable=False)
    status = Column(String(50), default="ok")
    image_base64 = Column(Text(16777215), nullable=True)
    product_type = Column(String(100), nullable=True)
    quantity = Column(Integer, nullable=True)
    unit_price = Column(Integer, nullable=True)
    total_price = Column(Integer, nullable=True)
    qr_uuid = Column(String(50), nullable=True) 
    session_id = Column(Integer, index=True, nullable=True)

class ScanSession(Base):
    __tablename__ = "scan_sessions"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    is_active = Column(Integer, default=1) # 1: active, 0: ended

# Ensure tables exist
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    logger.warning(f"Could not init db schema: {e}")

# Start fast API
app = FastAPI(title="QR Code Detection Industrial Process")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Thư mục lưu mô hình
_base_dir = os.path.dirname(os.path.abspath(__file__))
model_path = os.path.join(_base_dir, "best.pt")

if os.path.exists(model_path):
    logger.info(f"Loading custom model from {model_path}...")
    model = YOLO(model_path)
    using_custom_yolo = True
else:
    logger.warning(f"Cannot find custom model at {model_path}. Files: {os.listdir(_base_dir)}")
    model = YOLO('yolov8n.pt')
    using_custom_yolo = False

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Backend is running."}

@app.get("/sessions")
def get_sessions(db: Session = Depends(get_db)):
    sessions = db.query(ScanSession).order_by(ScanSession.start_time.desc()).all()
    from sqlalchemy import func
    counts = db.query(ScanResult.session_id, func.count(ScanResult.id)).group_by(ScanResult.session_id).all()
    count_map = {c[0]: c[1] for c in counts if c[0] is not None}
    return [
        {
            "id": s.id, "name": s.name, "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat() if s.end_time else None,
            "is_active": s.is_active, "total_scans": count_map.get(s.id, 0)
        } for s in sessions
    ]

@app.post("/sessions")
def create_session(name: str = None, db: Session = Depends(get_db)):
    if not name:
        name = f"Session {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    db.query(ScanSession).filter(ScanSession.is_active == 1).update({"is_active": 0, "end_time": datetime.utcnow()})
    new_sess = ScanSession(name=name, is_active=1)
    db.add(new_sess)
    db.commit()
    db.refresh(new_sess)
    return {"id": new_sess.id, "name": new_sess.name, "is_active": new_sess.is_active, "start_time": new_sess.start_time.isoformat(), "total_scans": 0}

@app.put("/sessions/{session_id}/end")
def end_session(session_id: int, db: Session = Depends(get_db)):
    sess = db.query(ScanSession).filter(ScanSession.id == session_id).first()
    if sess:
        sess.is_active = 0
        sess.end_time = datetime.utcnow()
        db.commit()
    return {"status": "ok"}

@app.get("/scans")
def get_scans(session_id: int = None, db: Session = Depends(get_db)):
    query = db.query(ScanResult)
    if session_id:
        query = query.filter(ScanResult.session_id == session_id)
    results = query.order_by(ScanResult.timestamp.desc()).all()
    return [
        {
            "id": s.id, "qr_content": s.qr_content, "confidence": s.confidence,
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            "method": s.method, "status": s.status, "image_base64": s.image_base64,
            "product_type": s.product_type, "quantity": s.quantity,
            "unit_price": s.unit_price, "total_price": s.total_price,
            "qr_uuid": s.qr_uuid, "session_id": s.session_id
        } for s in results
    ]

@app.delete("/sessions/{session_id}/scans")
def clear_session_scans(session_id: int, db: Session = Depends(get_db)):
    db.query(ScanResult).filter(ScanResult.session_id == session_id).delete()
    db.commit()
    return {"message": f"All scans for session {session_id} cleared"}

@app.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    db.query(ScanResult).filter(ScanResult.session_id == session_id).delete()
    sess = db.query(ScanSession).filter(ScanSession.id == session_id).first()
    if sess:
        db.delete(sess)
    db.commit()
    return {"message": f"Session {session_id} deleted"}

def _parse_factory(content: str):
    if not content or not content.startswith("FACTORY|"): return None, None, None, None, None
    parts = content.split("|")
    if len(parts) < 5: return None, None, None, None, None
    try:
        p_uuid, p_type = parts[1], parts[2]
        p_qty, p_unit = int(parts[3]), int(parts[4])
        return p_uuid, p_type, p_qty, p_unit, p_qty * p_unit
    except: return parts[1], parts[2], None, None, None

def _process_frame(frame, using_custom_yolo):
    results = []
    yolo_res = model(frame, conf=0.45, verbose=False)[0]
    boxes = []
    if using_custom_yolo and hasattr(yolo_res, 'boxes'):
        for b in yolo_res.boxes:
            if int(b.cls[0]) == 0 and float(b.conf[0]) >= 0.45:
                boxes.append(b)
    if boxes:
        blist = sorted([(int(b.xyxy[0][0]), int(b.xyxy[0][1]), int(b.xyxy[0][2]), int(b.xyxy[0][3]), float(b.conf[0])) for b in boxes], key=lambda x: -x[4])
        keep, sup = [], set()
        for i, bi in enumerate(blist):
            if i in sup: continue
            keep.append(i)
            for j, bj in enumerate(blist):
                if j != i and j not in sup and iou(bi[:4], bj[:4]) > 0.4: sup.add(j)
        for i in keep:
            x1, y1, x2, y2, conf = blist[i]
            content, method = smart_decode(frame, x1, y1, x2, y2)
            status = "ok" if content and len(content.strip()) >= 4 else ("invalid" if content else "failed")
            try:
                fh, fw = frame.shape[:2]
                crop = frame[max(0, y1):min(fh, y2), max(0, x1):min(fw, x2)]
                _, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                image_b64 = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
            except: image_b64 = ""
            results.append({"bbox": [x1, y1, x2, y2], "conf": conf, "content": content, "status": status, "method": method or "YOLO", "image_base64": image_b64})
    if not results:
        for obj in pyzbar_decode(frame):
            content = obj.data.decode("utf-8", errors="replace")
            r = obj.rect
            results.append({"bbox": [r.left, r.top, r.left+r.width, r.top+r.height], "conf": 0.95, "content": content, "status": "ok", "method": "Pyzbar", "image_base64": ""})
    return results

@app.websocket("/ws/detect")
async def websocket_detect(websocket: WebSocket, session_id: int = None):
    logger.info(f"WebSocket attempt: session_id={session_id}")
    await websocket.accept()
    logger.info(f"WebSocket accepted: session_id={session_id}")
    db: Session = SessionLocal()
    try:
        loop = asyncio.get_event_loop()
        active_targets, yolo_running, last_yolo_ms, pending_is_yolo = [], False, 0.0, False
        session_seen = set()

        async def _run_yolo(frame_copy):
            nonlocal yolo_running, active_targets, pending_is_yolo
            try:
                results = await loop.run_in_executor(_executor, _process_frame, frame_copy, using_custom_yolo)
                # Each YOLO cycle: completely replace active_targets with fresh detections.
                # Trackers are only used to smooth positions between YOLO cycles.
                new_targets = []
                for res in results:
                    t = _create_tracker()
                    x1, y1, x2, y2 = res["bbox"]
                    h, w = frame_copy.shape[:2]
                    tw = max(5, min(w - x1, x2 - x1))
                    th = max(5, min(h - y1, y2 - y1))
                    roi = (int(max(0, x1)), int(max(0, y1)), int(tw), int(th))
                    try:
                        tracker = t if t.init(frame_copy, roi) else None
                    except Exception:
                        tracker = None
                    new_targets.append({"tracker": tracker, "det": res, "fc": 0})

                active_targets[:] = new_targets[:8]

                if results:
                    pending_is_yolo = True
                    for d in results:
                        if d["status"] != "ok": continue
                        p_uuid, p_type, p_qty, p_unit, p_total = _parse_factory(d["content"])
                        key = p_uuid or d["content"]
                        if not key or key in session_seen: continue
                        session_seen.add(key)
                        try:
                            db.add(ScanResult(qr_content=d["content"], confidence=d["conf"], method=d["method"], status=d["status"], image_base64=d["image_base64"], product_type=p_type, quantity=p_qty, unit_price=p_unit, total_price=p_total, qr_uuid=p_uuid, session_id=session_id))
                            db.commit()
                        except: db.rollback()
            except Exception as e: logger.error(f"YOLO Error: {e}")
            finally: yolo_running = False

        while True:
            msg = await websocket.receive_text()
            try:
                # Frontend sends raw dataURL: "data:image/jpeg;base64,..."
                raw = msg.removeprefix("data:image/jpeg;base64,").removeprefix("data:image/png;base64,")
                frame = cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8), cv2.IMREAD_COLOR)
            except Exception as e:
                logger.warning(f"Frame decode error: {e}")
                continue
            if frame is None: continue
            
            now = time.time() * 1000
            # Smooth bounding box positions with tracker between YOLO cycles
            if active_targets:
                trackable = [t for t in active_targets if t["tracker"] is not None]
                if trackable:
                    res_list = await asyncio.gather(*[loop.run_in_executor(_executor, _track_frame, t["tracker"], frame) for t in trackable])
                    for i, (ok, box) in enumerate(res_list):
                        t = trackable[i]
                        if ok:
                            t["det"]["bbox"] = [int(box[0]), int(box[1]), int(box[0]+box[2]), int(box[1]+box[3])]
                            t["fc"] = 0
                        else:
                            t["fc"] += 1
                    # Remove targets whose tracker has consistently failed (stale)
                    active_targets[:] = [t for t in active_targets if t["tracker"] is None or t["fc"] < 5]

            if not yolo_running and now - last_yolo_ms >= 150:
                last_yolo_ms, yolo_running = now, True
                asyncio.create_task(_run_yolo(frame.copy()))

            await websocket.send_text(json.dumps({"detections": [t["det"] for t in active_targets], "using_custom_model": using_custom_yolo, "timestamp": datetime.utcnow().isoformat(), "is_yolo": pending_is_yolo}))
            pending_is_yolo = False
    except WebSocketDisconnect: pass
    except Exception as e: logger.error(f"WS Error: {e}")
    finally: db.close()
