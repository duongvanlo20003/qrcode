import React, { useEffect, useRef, useState } from 'react';
import { Camera, Server, ShieldCheck, AlignLeft, AlertCircle, PackageSearch, Globe, Hash, FolderOpen, X, Package } from 'lucide-react';

const parseFactoryQR = (content: string): Partial<ScanResult> => {
  if (!content || !content.startsWith('FACTORY|')) return {};
  const parts = content.split('|');
  if (parts.length < 5) return {};
  const qty = parseInt(parts[3], 10);
  const unit = parseInt(parts[4], 10);
  return {
    qr_uuid: parts[1] || undefined,
    product_type: parts[2] || undefined,
    quantity: isNaN(qty) ? undefined : qty,
    unit_price: isNaN(unit) ? undefined : unit,
    total_price: isNaN(qty) || isNaN(unit) ? undefined : qty * unit,
  };
};

const KNOWN_CATEGORIES = [
  'Thạch (Jelly)',
  'Kẹo dẻo (Gummy)',
  'Kẹo xốp (Marshmallow)',
  'Bánh quy (Biscuit)',
  'Bánh kem (Cake)',
];

const stripAccents = (s: string) =>
  s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();

const categorizeQR = (content: string): string => {
   if (!content || content.includes('FAILED')) return 'Lỗi / Hỏng';
   if (content.startsWith('FACTORY|')) {
      const parts = content.split('|');
      const raw = (parts[2] || '').trim();
      const match = KNOWN_CATEGORIES.find(c => stripAccents(c) === stripAccents(raw));
      return match || raw || 'Khác (Text)';
   }
   const up = content.toUpperCase();
   if (up.includes('HTTP://') || up.includes('HTTPS://')) return 'Website Link';
   return 'Khác (Text)';
}

const CAT_COLORS: Record<string, { bg: string, text: string, icon: any }> = {
  'Thạch (Jelly)': { bg: 'bg-indigo-500/10', text: 'text-indigo-400', icon: PackageSearch },
  'Kẹo dẻo (Gummy)': { bg: 'bg-emerald-500/10', text: 'text-emerald-400', icon: PackageSearch },
  'Kẹo xốp (Marshmallow)': { bg: 'bg-sky-500/10', text: 'text-sky-400', icon: Globe },
  'Bánh quy (Biscuit)': { bg: 'bg-fuchsia-500/10', text: 'text-fuchsia-400', icon: Hash },
  'Bánh kem (Cake)': { bg: 'bg-orange-500/10', text: 'text-orange-400', icon: FolderOpen },
  'Website Link': { bg: 'bg-sky-500/10', text: 'text-sky-400', icon: Globe },
  'Khác (Text)': { bg: 'bg-slate-500/20', text: 'text-slate-300', icon: FolderOpen },
};

interface Detection {
  bbox: [number, number, number, number];
  conf: number;
  content: string | null;
  status: string;
  method: string;
  image_base64?: string;
}

interface ScanResult {
  id: number;
  qr_content: string;
  confidence: number;
  timestamp: string;
  method: string;
  status?: string;
  image_base64?: string;
  count?: number;
  product_type?: string;
  quantity?: number;
  unit_price?: number;
  total_price?: number;
  qr_uuid?: string;
  session_id?: number;
}

interface ScanSession {
  id: number;
  name: string;
  start_time: string;
  end_time?: string;
  is_active: number;
  total_scans: number;
}

function App() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const captureCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement>(null);
  // Ref updated by socket.onmessage, drawn by rAF loop — no React re-render needed
  const serverDetectionsRef = useRef<Detection[]>([]);
  const failCountRef = useRef<Map<string, number>>(new Map());
  const lastServerUpdateRef = useRef<number>(0);
  
  const [ws, setWs] = useState<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [isCustomModel, setIsCustomModel] = useState(false); // Thêm trạng thái model
  const [history, setHistory] = useState<ScanResult[]>([]);
  const [selectedScan, setSelectedScan] = useState<ScanResult | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  const [categories, setCategories] = useState<Record<string, number>>({
      'Thạch (Jelly)': 0,
      'Kẹo dẻo (Gummy)': 0,
      'Kẹo xốp (Marshmallow)': 0,
      'Bánh quy (Biscuit)': 0,
      'Bánh kem (Cake)': 0
  });

  const [qtyFilter, setQtyFilter] = useState<number | null>(null);
  const [showResetDialog, setShowResetDialog] = useState(false);
  const [resetConfirmText, setResetConfirmText] = useState('');

  // SESSIONS STATE
  const [sessions, setSessions] = useState<ScanSession[]>([]);
  const [activeSession, setActiveSession] = useState<ScanSession | null>(null);
  const [viewingSession, setViewingSession] = useState<ScanSession | null>(null);
  const [isStartingSession, setIsStartingSession] = useState(false);

  const host = window.location.hostname;
  const API_URL = `http://${host}:8000`;

  // 1. Fetch sessions on mount
  useEffect(() => {
    fetch(`${API_URL}/sessions`)
      .then(r => r.json())
      .then((data: ScanSession[]) => {
        setSessions(data);
        const active = data.find(s => s.is_active === 1);
        if (active) {
            setActiveSession(active);
            setViewingSession(active);
        }
      });
  }, []);

  // 2. Fetch history when viewingSession changes
  useEffect(() => {
    if (!viewingSession) return;
    
    console.log(`Fetching history for session ${viewingSession.id}...`);
    fetch(`${API_URL}/scans?session_id=${viewingSession.id}`)
      .then(r => r.json())
      .then((data: ScanResult[]) => {
        setHistory(data);
        
        const newCats: Record<string, number> = {
            'Thạch (Jelly)': 0, 'Kẹo dẻo (Gummy)': 0, 'Kẹo xốp (Marshmallow)': 0, 'Bánh quy (Biscuit)': 0, 'Bánh kem (Cake)': 0
        };
        
        let failures = 0;
        const uniqueSet = new Set<string>();
        data.forEach(d => {
           if (d.qr_content && d.qr_content !== 'FAILED_DECODE') {
             uniqueSet.add(d.qr_content);
             const cat = categorizeQR(d.qr_content);
             if (cat !== 'Lỗi / Hỏng' && newCats[cat] !== undefined) {
               newCats[cat]++;
             }
           } else {
             failures++;
           }
        });

        setCategories(newCats);
      })
      .catch(e => console.error("Error fetching session scans:", e));
  }, [viewingSession]);

  // Handle Start Session
  const startSession = async () => {
    setIsStartingSession(true);
    console.log("Starting new session...");
    try {
        const r = await fetch(`${API_URL}/sessions`, { method: 'POST' });
        if (!r.ok) {
            const errBody = await r.text();
            throw new Error(`Failed to start session: ${r.status} - ${errBody}`);
        }
        const newSess = await r.json();
        console.log("New session created:", newSess);
        setActiveSession(newSess);
        setViewingSession(newSess);
        
        // Refresh sessions list
        const listR = await fetch(`${API_URL}/sessions`);
        if (listR.ok) setSessions(await listR.json());
    } catch (e) {
        console.error("Critical error starting session:", e);
        alert("Lỗi: Không thể khởi tạo phiên quét mới. Vui lòng kiểm tra kết nối Backend.");
    } finally {
        setIsStartingSession(false);
    }
  };

  // Handle End Session
  const endSession = async () => {
    if (!activeSession) return;
    await fetch(`${API_URL}/sessions/${activeSession.id}/end`, { method: 'PUT' });
    setActiveSession(null);
    // Refresh sessions
    const listR = await fetch(`${API_URL}/sessions`);
    setSessions(await listR.json());
  };

  // 3. Setup WebSocket (only if activeSession exists)
  useEffect(() => {
    if (!activeSession) {
        if (ws) {
            ws.close();
            setWs(null);
            setIsConnected(false);
        }
        return;
    }

    const host = window.location.hostname;
    const wsUrl = `ws://${host}:8000/ws/detect?session_id=${activeSession.id}`;
    let socket = new WebSocket(wsUrl);
    
    socket.onopen = () => {
      setIsConnected(true);
      console.log('WS Connected');
    };
    
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.detections) {
        // Stabilize "failed" status: only show failed after 4 consecutive failed frames
        // so a blurry first frame doesn't immediately flash red
        const FAIL_THRESHOLD = 4;
        const stabilized: Detection[] = data.detections.map((d: Detection) => {
          const cx = Math.round(((d.bbox[0] + d.bbox[2]) / 2) / 40) * 40;
          const cy = Math.round(((d.bbox[1] + d.bbox[3]) / 2) / 40) * 40;
          const key = `${cx}_${cy}`;
          const prev = failCountRef.current.get(key) ?? 0;
          if (d.status === 'ok') {
            failCountRef.current.set(key, 0);
            return d;
          }
          const count = prev + 1;
          failCountRef.current.set(key, count);
          return { ...d, status: count >= FAIL_THRESHOLD ? d.status : 'scanning' };
        });
        // Evict stale keys for boxes that disappeared this frame
        const activeKeys = new Set(stabilized.map((d: Detection) => {
          const cx = Math.round(((d.bbox[0] + d.bbox[2]) / 2) / 40) * 40;
          const cy = Math.round(((d.bbox[1] + d.bbox[3]) / 2) / 40) * 40;
          return `${cx}_${cy}`;
        }));
        failCountRef.current.forEach((_, k) => { if (!activeKeys.has(k)) failCountRef.current.delete(k); });

        // Update ref for 60fps canvas loop (no re-render needed for bbox drawing)
        serverDetectionsRef.current = stabilized;
        lastServerUpdateRef.current = Date.now(); // Force Docker cache bust


        setIsCustomModel(data.using_custom_model || false);
        
        // Chỉ lưu vào history khi QR được đọc thành công (status=ok)
        // Bỏ qua 'failed' - đây là noise từ camera, không phải scan thực sự
        let newHistory : ScanResult[] = [];
        data.detections.forEach((d: Detection) => {
          if (d.status === 'ok' && d.content && d.content.length >= 3) {
            newHistory.push({
               id: Date.now() + Math.random(),
               qr_content: d.content,
               confidence: d.conf,
               timestamp: new Date().toISOString(),
               method: d.method,
               status: d.status,
               image_base64: d.image_base64,
               count: 1,
               ...parseFactoryQR(d.content || ""),
            });
          }
        });
        
        if (newHistory.length > 0) {
           setHistory(prev => {
              const updatedHistory = [...prev];
              
              newHistory.forEach(newItem => {
                  // Sử dụng UUID làm khóa chính nếu có, nếu không thì dùng nội dung QR
                  const identifier = newItem.qr_uuid && newItem.qr_uuid !== '' ? newItem.qr_uuid : newItem.qr_content;
                  const existingIdx = updatedHistory.findIndex(h => {
                      const hId = h.qr_uuid && h.qr_uuid !== '' ? h.qr_uuid : h.qr_content;
                      return hId === identifier;
                  });
                  
                  if (existingIdx !== -1) {
                      // Nếu đã có UUID (thùng hàng cụ thể), tuyệt đối KHÔNG tăng count nữa để tránh spam khi để cam lâu
                      // Chúng ta chỉ cập nhật count cho các trường hợp FAILED_DECODE (không có UUID)
                      const isNoUuid = !identifier || identifier.startsWith('FAILED_BOX') || identifier === 'FAILED_DECODE';
                      
                      updatedHistory[existingIdx] = {
                          ...updatedHistory[existingIdx],
                          confidence: newItem.confidence,
                          timestamp: newItem.timestamp,
                          image_base64: newItem.image_base64 || updatedHistory[existingIdx].image_base64,
                          count: isNoUuid ? (updatedHistory[existingIdx].count || 1) + 1 : 1
                      };
                  } else {
                      // Nếu chưa có, thêm mới vào đầu danh sách
                      updatedHistory.unshift({ ...newItem, count: 1 });
                      
                      setCategories(cPrev => {
                         const cNext = { ...cPrev };
                         const cat = categorizeQR(newItem.qr_content || "");
                         if (cat !== 'Lỗi / Hỏng') cNext[cat] = (cNext[cat] || 0) + 1;
                         return cNext;
                      });
                  }
              });
              
              return updatedHistory.slice(0, 50);
           });
         }
       }
     };
    
    socket.onclose = () => setIsConnected(false);
    socket.onerror = () => setIsConnected(false);
    
    setWs(socket);
    
    return () => {
      socket.close();
    };
  }, [activeSession]);

  // WebRTC Setup & Capture Loop
  useEffect(() => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;

    let animationFrameId: number;
    let stream: MediaStream;

    navigator.mediaDevices.getUserMedia({ 
      video: { facingMode: "environment", width: 640, height: 480 } 
    }).then(s => {
      stream = s;
      if (videoRef.current) {
        videoRef.current.srcObject = s;
        videoRef.current.play();
      }
    }).catch(err => console.error("Webcam error:", err));

    const drawDet = (oc: CanvasRenderingContext2D, det: Detection) => {
      const [x1, y1, x2, y2] = det.bbox;
      const w = x2 - x1, h = y2 - y1;
      let color = '#38bdf8';
      if (det.status === 'scanning') color = '#f59e0b';
      if (det.status === 'duplicate') color = '#a78bfa';
      if (det.status === 'failed' || det.status === 'invalid') color = '#ef4444';
      if (det.status === 'ok') color = '#22c55e';
      oc.lineWidth = 3;
      oc.strokeStyle = color;
      oc.strokeRect(x1, y1, w, h);
      oc.fillStyle = color;
      const label = det.status === 'scanning' ? 'SCANNING...' : det.status.toUpperCase();
      const text = `${label} | ${(det.conf * 100).toFixed(0)}%`;
      oc.fillRect(x1, y1 > 20 ? y1 - 20 : y1, w, 20);
      oc.fillStyle = '#020617';
      oc.font = 'bold 14px monospace';
      oc.fillText(text, x1 + 4, y1 > 20 ? y1 - 6 : y1 + 14);
      if (det.content) {
        oc.fillStyle = color;
        oc.fillText(det.content.length > 28 ? det.content.substring(0, 28) + '...' : det.content, x1, y2 + 18);
      }
    };

    let lastSendMs = 0;

    const captureLoop = () => {
      const video = videoRef.current;
      const capture = captureCanvasRef.current;
      const overlay = overlayCanvasRef.current;

      if (video && capture && video.readyState === video.HAVE_ENOUGH_DATA) {
        const vidW = video.videoWidth;
        const vidH = video.videoHeight;
        const ctx = capture.getContext('2d', { alpha: false });
        if (ctx && vidW && vidH) {
          if (capture.width !== vidW || capture.height !== vidH) {
            capture.width = vidW;
            capture.height = vidH;
          }
          ctx.drawImage(video, 0, 0, vidW, vidH);

          // ── Draw overlay every frame from server refs (30-60fps smooth) ──
          if (overlay) {
            const oc = overlay.getContext('2d');
            if (oc) {
              if (overlay.width !== vidW || overlay.height !== vidH) {
                overlay.width = vidW;
                overlay.height = vidH;
              }
              oc.clearRect(0, 0, vidW, vidH);

              // Only show server detections if they are fresh (last 500ms)
              if (Date.now() - lastServerUpdateRef.current < 500) {
                serverDetectionsRef.current.forEach(d => drawDet(oc, d));
              }
            }
          }

          // ── Backend send: 30fps (every ~33ms) ──
          const now = Date.now();
          if (ws && ws.readyState === WebSocket.OPEN && now - lastSendMs >= 33) {
            lastSendMs = now;
            const dataUrl = capture.toDataURL('image/jpeg', 0.5);
            ws.send(dataUrl);
            
            // Log every ~3 seconds to confirm movement
            if (Math.random() < 0.01) {
              console.log('WS: Frame sent to backend');
            }
          }
        }
      }

      animationFrameId = requestAnimationFrame(captureLoop);
    };

    animationFrameId = requestAnimationFrame(captureLoop);

    return () => {
      cancelAnimationFrame(animationFrameId);
      if (stream) stream.getTracks().forEach(t => t.stop());
    };
  }, [ws]);



  return (
    <div className="flex flex-col h-screen max-h-screen overflow-hidden bg-slate-950 text-slate-200">
      {/* HEADER */}
      <header className="px-8 py-5 border-b border-white/5 bg-slate-900/50 flex justify-between items-center z-10 backdrop-blur-md">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl bg-sky-400/10 border-2 border-sky-400 flex items-center justify-center">
            <Camera className="text-sky-400" size={24} strokeWidth={2.5} />
          </div>
          <div>
            <p className="text-sky-400 font-extrabold tracking-widest text-xs uppercase mb-1">Industrial Computer Vision</p>
            <h1 className="text-2xl font-black text-white leading-none tracking-tight">QR Scanner System</h1>
          </div>
        </div>
        
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className={`w-3 h-3 rounded-full ${isConnected ? 'bg-green-500 shadow-[0_0_12px_rgba(34,197,94,0.6)] animate-pulse' : 'bg-red-500'}`} />
            <span className="font-bold text-sm tracking-wide text-slate-300">
              {isConnected ? 'SYSTEM LIVE' : 'OFFLINE'}
            </span>
          </div>
          
          <div className="flex gap-2">
            {activeSession && (
              <button
                 onClick={endSession}
                 className="bg-red-500/20 hover:bg-red-500/30 text-red-500 font-bold text-xs px-4 py-2 rounded-lg transition-all flex items-center gap-2 border border-red-500/40 shadow-lg shadow-red-500/10"
              >
                END SESSION
              </button>
            )}
            <button
               onClick={() => { setShowResetDialog(true); setResetConfirmText(''); }}
               className="bg-slate-800 hover:bg-slate-700 text-slate-400 font-bold text-xs px-3 py-2 rounded-lg transition-colors border border-white/5"
            >
              RESET DB
            </button>
          </div>
        </div>
      </header>

      {/* MAIN CONTENT */}
      <main className="flex-1 flex overflow-hidden">
        
        {/* SESSION SIDEBAR */}
        <aside className="w-80 bg-slate-900/30 border-r border-white/5 flex flex-col overflow-hidden">
            <div className="p-6 border-b border-white/5">
                <h3 className="text-xs font-black text-slate-500 uppercase tracking-widest mb-4">All Scan Sessions</h3>
                {activeSession ? (
                    <div className="bg-sky-500/10 border border-sky-400/30 rounded-2xl p-4 mb-2">
                        <p className="text-[10px] font-bold text-sky-400 uppercase mb-1">Active Session</p>
                        <p className="font-black text-white text-sm truncate">{activeSession.name}</p>
                    </div>
                ) : (
                    <button 
                        onClick={startSession}
                        disabled={isStartingSession}
                        className="w-full py-3 bg-sky-500 hover:bg-sky-400 text-slate-950 font-black rounded-xl transition-all shadow-lg shadow-sky-500/20 active:scale-95 disabled:opacity-50"
                    >
                        {isStartingSession ? 'STARTING...' : '+ NEW SESSION'}
                    </button>
                )}
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
                {sessions.map(s => (
                    <button 
                        key={s.id}
                        onClick={() => setViewingSession(s)}
                        className={`w-full text-left p-4 rounded-2xl border transition-all group ${viewingSession?.id === s.id ? 'bg-slate-800 border-sky-400/50 shadow-xl' : 'bg-transparent border-white/5 hover:bg-white/5'}`}
                    >
                        <div className="flex justify-between items-start mb-1">
                            <p className={`font-bold text-sm truncate flex-1 ${viewingSession?.id === s.id ? 'text-white' : 'text-slate-400 group-hover:text-slate-200'}`}>{s.name}</p>
                            {s.is_active === 1 && <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse mt-1 ml-2" />}
                        </div>
                        <div className="flex items-center justify-between">
                            <p className="text-[10px] font-medium text-slate-500">{new Date(s.start_time).toLocaleDateString()}</p>
                            <span className="text-[10px] font-black bg-slate-800 px-2 py-0.5 rounded text-slate-400 group-hover:text-sky-400">{s.total_scans} scans</span>
                        </div>
                    </button>
                ))}
            </div>
        </aside>

        {/* DASHBOARD AREA */}
        <div className="flex-1 p-8 grid grid-cols-1 lg:grid-cols-12 gap-8 overflow-hidden relative">
          
          {/* START SESSION OVERLAY */}
          {!activeSession && (
            <div className="absolute inset-0 z-20 bg-slate-950/60 backdrop-blur-md flex items-center justify-center p-8">
                <div className="max-w-md w-full text-center bg-slate-900 border border-white/10 p-10 rounded-[40px] shadow-2xl animate-in zoom-in-95 duration-500">
                    <div className="w-20 h-20 rounded-3xl bg-sky-400/10 border border-sky-400/30 flex items-center justify-center mx-auto mb-6">
                        <Package size={40} className="text-sky-400" />
                    </div>
                    <h2 className="text-3xl font-black text-white mb-2">Ready to Scan?</h2>
                    <p className="text-slate-400 text-sm mb-8 leading-relaxed">System is currently offline. Start a new session to begin industrial monitoring and yield reporting.</p>
                    <button 
                        onClick={startSession}
                        disabled={isStartingSession}
                        className="w-full py-4 bg-sky-500 hover:bg-sky-400 text-slate-950 font-black text-lg rounded-2xl transition-all shadow-[0_0_30px_rgba(56,189,248,0.3)] hover:scale-[1.02] active:scale-95 disabled:opacity-50"
                    >
                        {isStartingSession ? 'INITIALIZING...' : 'START NEW SCAN SESSION'}
                    </button>
                    <div className="mt-8 pt-8 border-t border-white/5">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest">Or select a previous session from the sidebar</p>
                    </div>
                </div>
            </div>
          )}

          {/* LEFT COLUMN: CAMERA ONSCREEN */}
          <section className="lg:col-span-8 flex flex-col gap-6">
          <div className="relative flex-1 bg-slate-900 rounded-3xl border border-sky-400/20 overflow-hidden shadow-[0_20px_50px_rgba(0,0,0,0.5)]">
            <video 
              ref={videoRef} 
              autoPlay 
              playsInline 
              muted 
              className="absolute inset-0 w-full h-full object-contain bg-black"
            />
            {/* Hidden canvas for capturing frames */}
            <canvas ref={captureCanvasRef} className="hidden" />
            {/* Transparent canvas for drawing boxes */}
            <canvas
              ref={overlayCanvasRef}
              className="absolute inset-0 w-full h-full object-contain pointer-events-none"
            />
            
            {/* Overlay UI elements inside camera view */}
             <div className="absolute top-6 left-6 flex flex-col gap-2">
                <div className="bg-slate-950/80 backdrop-blur-md px-4 py-2 rounded-lg border border-white/10 flex items-center gap-3">
                  <Server size={16} className={isCustomModel ? "text-green-400" : "text-amber-400"} />
                  <span className="font-mono font-bold text-sm">
                    {isCustomModel ? "YOLOv8 + Custom Model (Deep Learning)" : "YOLOv8 Fallback (Sensitive Mode)"}
                  </span>
                </div>
                {!isCustomModel && (
                  <div className="bg-amber-500/10 backdrop-blur-md px-4 py-2 rounded-lg border border-amber-500/30 flex items-center gap-2">
                    <AlertCircle size={14} className="text-amber-400" />
                    <span className="text-[10px] font-bold text-amber-200 uppercase tracking-tighter">
                      Warning: best.pt not found. Wall detection may occur.
                    </span>
                  </div>
                )}
             </div>
          </div>
          
          {/* LIVE YIELD CATEGORIES */}
          <div className="bg-slate-900/50 rounded-3xl p-5 border border-white/5">
            <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-3">Live Yield Metrics</h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
              {Object.entries(categories).map(([name, count]) => {
                const style = CAT_COLORS[name] || CAT_COLORS['Khác (Text)'];
                const Icon = style.icon;
                return (
                 <div 
                  key={name} 
                  onClick={() => setSelectedCategory(name)}
                  className={`${style.bg} border border-white/5 rounded-2xl p-4 flex flex-col items-center justify-center relative overflow-hidden group hover:border-${style.text.split('-')[1]}-500/30 transition-all cursor-pointer hover:scale-[1.02] active:scale-95`}
                 >
                    <Icon className={`${style.text} mb-2 opacity-80 group-hover:scale-110 transition-transform`} size={24} />
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest text-center whitespace-nowrap z-10">{name}</span>
                    <span className={`text-3xl font-black ${style.text} leading-none mt-1 z-10`}>{count}</span>
                 </div>
                )
              })}
            </div>
          </div>
        </section>

        {/* RIGHT COLUMN: METRICS & HISTORY */}
        <section className="lg:col-span-4 flex flex-col gap-6 overflow-hidden h-full">
          {/* METRICS */}
          <div className="grid grid-cols-3 gap-4">
             <div className="bg-gradient-to-br from-indigo-500/10 to-transparent p-4 rounded-3xl border border-white/5 text-center">
                <div className="text-3xl font-black text-indigo-400 leading-none mb-1">{history.length}</div>
                <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Total Scans</div>
             </div>
             <div className="bg-gradient-to-br from-sky-400/10 to-slate-900/80 p-4 rounded-3xl border border-sky-400/30 text-center">
                <div className="text-3xl font-black text-sky-400 leading-none mb-1">{history.filter(h => h.status === 'ok').length}</div>
                <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Unique QR</div>
             </div>
             <div className="bg-gradient-to-br from-slate-800/50 to-slate-900/80 p-4 rounded-2xl border border-white/5 text-center">
                <div className="text-3xl font-black text-slate-100 leading-none mb-1">{history.filter(h => h.status === 'failed' || h.qr_content === 'FAILED_DECODE').length}</div>
                <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Failures</div>
             </div>
          </div>

          <div className="bg-slate-900/60 rounded-3xl border border-white/5 overflow-hidden flex flex-col flex-1 min-h-0">
            <div className="p-5 border-b border-white/5 flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <h3 className="font-bold text-white flex items-center gap-2">
                  <AlignLeft size={18} className="text-sky-400" />
                  Conveyor Audit Trail
                </h3>
                <span className="text-xs font-bold text-slate-400 bg-slate-800 px-3 py-1 rounded-full uppercase">Realtime</span>
              </div>
              
              {/* FILTER BUTTONS */}
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-black text-slate-500 uppercase">Filter Qty:</span>
                <div className="flex gap-1">
                  {[50, 100, 200, 500].map(q => (
                    <button 
                      key={q}
                      onClick={() => setQtyFilter(qtyFilter === q ? null : q)}
                      className={`text-[10px] font-black px-2.5 py-1 rounded-md transition-all border ${qtyFilter === q ? 'bg-sky-400 text-slate-950 border-sky-400 shadow-[0_0_10px_rgba(56,189,248,0.3)]' : 'bg-slate-800/50 text-slate-400 border-white/5 hover:border-white/10'}`}
                    >
                      {q}P
                    </button>
                  ))}
                  {qtyFilter && (
                    <button onClick={() => setQtyFilter(null)} className="text-[10px] font-bold text-sky-400 hover:underline ml-1">Clear</button>
                  ) }
                </div>
              </div>
            </div>
            
            <div className="flex-1 overflow-y-auto p-2 scroll-smooth">
              <div className="flex flex-col gap-2">
                {history.length === 0 ? (
                  <div className="text-center p-8 text-slate-500 font-medium">No scan history recorded.</div>
                ) : (
                  history
                    .filter(scan => !qtyFilter || scan.quantity === qtyFilter)
                    .slice(0, 50).map((scan, i) => {
                    const isFailed = scan.status === 'failed' || scan.status === 'invalid' || scan.qr_content === 'FAILED_DECODE';
                    return (
                    <div 
                      key={i} 
                      onClick={() => setSelectedScan(scan)}
                      className="p-4 rounded-xl bg-slate-800/40 border border-white/5 flex items-start gap-4 cursor-pointer hover:bg-slate-700/50 transition-colors"
                    >
                       <div className={`p-2 rounded-lg border ${isFailed ? 'bg-red-500/10 text-red-500 border-red-500/20' : 'bg-green-500/10 text-green-500 border-green-500/20'}`}>
                          {isFailed ? <AlertCircle size={20} /> : <ShieldCheck size={20} />}
                       </div>
                       <div className="flex-1 min-w-0">
                          <div className="flex justify-between items-start">
                            <p className={`font-mono text-sm truncate font-bold ${isFailed ? 'text-red-400' : 'text-slate-200'}`}>
                              {scan.product_type || scan.qr_content}
                            </p>
                            <div className="flex items-center gap-1">
                              {scan.quantity && (
                                <span className="bg-slate-800 text-slate-400 text-[9px] px-1 py-0.5 rounded border border-white/5 font-bold">
                                  {scan.quantity} Gói
                                </span>
                              )}
                              {scan.count && scan.count > 1 && (
                                <span className="bg-sky-500/20 text-sky-400 text-[10px] px-1.5 py-0.5 rounded-md font-black animate-pulse">
                                  x{scan.count}
                                </span>
                              )}
                            </div>
                          </div>
                          
                          {scan.total_price ? (
                            <p className="text-[10px] text-green-400 font-bold mt-1">
                              SẢN LƯỢNG: {scan.total_price.toLocaleString()}đ
                            </p>
                          ) : null}
                          <div className="flex items-center gap-2 mt-2 text-[11px] font-bold">
                            <span className="bg-slate-950 px-2 py-0.5 rounded text-sky-400 border border-sky-400/20">
                              {(scan.confidence * 100).toFixed(1)}% Conf
                            </span>
                            {!isFailed && (() => {
                               const catName = categorizeQR(scan.qr_content);
                               const style = CAT_COLORS[catName] || CAT_COLORS['Khác (Text)'];
                               return (
                                  <span className={`${style.bg} ${style.text} px-2 py-0.5 rounded border border-white/5`}>
                                     {catName}
                                  </span>
                               )
                            })()}
                            <span className="uppercase text-slate-500 flex items-center gap-1 ml-auto">
                              <AlertCircle size={10}/> {scan.method.replace(' (Fallback)','')}
                            </span>
                          </div>
                       </div>
                    </div>
                  )})
                )}
              </div>
            </div>
          </div>
        </section>

        </div>
      </main>

      {/* AUDIT MODAL */}
      {selectedScan && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => setSelectedScan(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-3xl overflow-hidden max-w-xl w-full shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="p-6 border-b border-slate-800 flex justify-between items-center">
              <div>
                <h2 className="text-lg font-black text-white">Audit Details</h2>
                <p className="text-xs text-slate-400 font-mono mt-1">{new Date(selectedScan.timestamp).toLocaleString()}</p>
              </div>
              <button onClick={() => setSelectedScan(null)} className="text-slate-400 hover:text-white bg-slate-800 px-3 py-1 rounded-lg">Close</button>
            </div>
            <div className="p-6 flex flex-col gap-6">
              <div className="flex flex-col gap-2">
                <span className="text-xs font-bold text-slate-500 uppercase tracking-widest">QR Content</span>
                <div className={`p-4 rounded-xl font-mono text-sm break-all border ${selectedScan.qr_content === 'FAILED_DECODE' ? 'bg-red-500/10 border-red-500/20 text-red-400' : 'bg-slate-950 border-slate-800 text-slate-300'}`}>
                  {selectedScan.qr_content}
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                 <div>
                    <span className="text-xs font-bold text-slate-500 uppercase tracking-widest">Confidence</span>
                    <p className="text-2xl font-black text-sky-400 mt-1">{(selectedScan.confidence * 100).toFixed(1)}%</p>
                 </div>
                 <div>
                    <span className="text-xs font-bold text-slate-500 uppercase tracking-widest">Method</span>
                    <p className="text-sm font-bold text-slate-300 mt-3">{selectedScan.method}</p>
                 </div>
              </div>
              
              <div>
                <span className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-2 block">Captured Evidence Image</span>
                {selectedScan.image_base64 ? (
                  <div className="rounded-xl overflow-hidden border border-slate-800 bg-slate-950 flex items-center justify-center min-h-[200px]">
                    <img src={selectedScan.image_base64} alt="Evidence" className="max-w-full max-h-[300px] object-contain" />
                  </div>
                ) : (
                  <div className="rounded-xl border border-slate-800 border-dashed bg-slate-900 flex items-center justify-center py-12">
                    <p className="text-slate-500 font-medium text-sm">No image captured for this scan</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* CATEGORY DETAIL MODAL */}
      {selectedCategory && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-6 backdrop-blur-xl bg-slate-950/40 animate-in fade-in duration-300">
           <div className="bg-slate-900 border border-white/10 rounded-[40px] w-full max-w-xl max-h-[85vh] overflow-hidden shadow-2xl flex flex-col animate-in zoom-in-95 duration-300">
              <div className="p-8 border-b border-white/5 flex justify-between items-center bg-gradient-to-r from-sky-400/10 to-transparent">
                 <div className="flex items-center gap-4">
                    <div className={`p-4 rounded-2xl ${CAT_COLORS[selectedCategory]?.bg || 'bg-slate-800'}`}>
                       {React.createElement(CAT_COLORS[selectedCategory]?.icon || Package, { size: 32, className: CAT_COLORS[selectedCategory]?.text || 'text-slate-400' })}
                    </div>
                    <div>
                       <h2 className="text-2xl font-black text-white">{selectedCategory}</h2>
                       <p className="text-xs font-bold text-slate-500 uppercase tracking-widest">Production Yield Report</p>
                    </div>
                 </div>
                 <button onClick={() => setSelectedCategory(null)} className="p-2 hover:bg-white/5 rounded-full transition-colors text-slate-400 hover:text-white">
                    <X size={24} />
                 </button>
              </div>
              
              <div className="p-8 flex-1 overflow-y-auto">
                 <div className="grid grid-cols-2 gap-4 mb-8">
                    <div className="bg-slate-800/50 p-6 rounded-3xl border border-white/5">
                        <p className="text-[10px] font-bold text-slate-500 uppercase mb-1">Total Boxes</p>
                        <p className="text-3xl font-black text-white">{categories[selectedCategory] || 0}</p>
                    </div>
                    <div className="bg-sky-400/10 p-6 rounded-3xl border border-sky-400/20">
                        <p className="text-[10px] font-bold text-sky-400/60 uppercase mb-1">Total Category Value</p>
                        <p className="text-3xl font-black text-sky-400">
                          {history
                            .filter(s => categorizeQR(s.qr_content) === selectedCategory)
                            .reduce((sum, s) => sum + (s.total_price || 0), 0)
                            .toLocaleString()}đ
                        </p>
                    </div>
                 </div>

                 <h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-4">Breakdown by Quantity</h3>
                 <div className="space-y-3">
                    {[50, 100, 200, 500].map(q => {
                        const items = history.filter(s => categorizeQR(s.qr_content) === selectedCategory && s.quantity === q);
                        if (items.length === 0) return null;
                        const unitPrice = items[0].unit_price || 0;
                        const subTotal = items.reduce((sum, s) => sum + (s.total_price || 0), 0);
                        return (
                          <div key={q} className="flex items-center justify-between p-4 rounded-2xl bg-slate-800/30 border border-white/5 group hover:bg-slate-800/50 transition-colors">
                            <div className="flex items-center gap-3">
                               <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center font-bold text-sky-400 border border-white/5">
                                  {q}P
                               </div>
                               <div>
                                  <p className="font-bold text-slate-200">Thùng {q} gói</p>
                                  <p className="text-[10px] text-slate-500 font-medium">Giá mỗi thùng: ( {q} x {unitPrice.toLocaleString()}đ ) = {(q * unitPrice).toLocaleString()}đ</p>
                               </div>
                            </div>
                            <div className="text-right">
                               <p className="font-black text-white">{items.length} thùng</p>
                               <p className="text-xs font-bold text-green-400">{subTotal.toLocaleString()}đ</p>
                            </div>
                          </div>
                        )
                    })}
                    {Object.entries([50, 100, 200, 500]).every(([_, q]) => history.filter(s => categorizeQR(s.qr_content) === selectedCategory && s.quantity === q).length === 0) && (
                      <div className="text-center py-8 text-slate-500 font-bold italic opacity-50">No quantity data available for this category</div>
                    )}
                 </div>
              </div>

              <div className="p-8 bg-slate-950/50 border-t border-white/5">
                 <button 
                  onClick={() => setSelectedCategory(null)}
                  className="w-full py-4 bg-sky-500 hover:bg-sky-400 text-slate-950 font-black rounded-2xl transition-all hover:shadow-[0_0_20px_rgba(56,189,248,0.4)]"
                 >
                    CLOSE REPORT
                 </button>
              </div>
           </div>
        </div>
      )}

      {/* RESET CONFIRM DIALOG */}
      {showResetDialog && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-6 bg-slate-950/80 backdrop-blur-sm" onClick={() => setShowResetDialog(false)}>
          <div className="bg-slate-900 border border-red-500/30 rounded-3xl w-full max-w-md shadow-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="p-6 border-b border-slate-800 flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-red-500/10 border border-red-500/30 flex items-center justify-center flex-shrink-0">
                <AlertCircle size={24} className="text-red-500" />
              </div>
              <div>
                <h2 className="text-lg font-black text-white">Xác nhận xoá dữ liệu</h2>
                <p className="text-xs text-slate-400 mt-0.5">Hành động này không thể hoàn tác.</p>
              </div>
            </div>
            <div className="p-6 flex flex-col gap-5">
              <p className="text-sm text-slate-300">
                Toàn bộ lịch sử quét và số liệu thống kê sẽ bị xoá vĩnh viễn.
                Nhập <span className="font-mono font-black text-red-400">confirm</span> để tiếp tục.
              </p>
              <input
                type="text"
                autoFocus
                value={resetConfirmText}
                onChange={e => setResetConfirmText(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && resetConfirmText === 'confirm') {
                    fetch(`${API_URL}/scans`, { method: 'DELETE' }).then(() => {
                      setHistory([]);
                      setCategories({ 'Thạch (Jelly)': 0, 'Kẹo dẻo (Gummy)': 0, 'Kẹo xốp (Marshmallow)': 0, 'Bánh quy (Biscuit)': 0, 'Bánh kem (Cake)': 0 });
                      setShowResetDialog(false);
                    });
                  }
                }}
                placeholder="Nhập confirm..."
                className="w-full bg-slate-950 border border-slate-700 focus:border-red-500 outline-none rounded-xl px-4 py-3 font-mono text-sm text-white placeholder-slate-600 transition-colors"
              />
              <div className="flex gap-3">
                <button
                  onClick={() => setShowResetDialog(false)}
                  className="flex-1 py-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold text-sm transition-colors"
                >
                  Huỷ
                </button>
                <button
                  disabled={resetConfirmText !== 'confirm'}
                  onClick={() => {
                    fetch(`${API_URL}/scans`, { method: 'DELETE' }).then(() => {
                      setHistory([]);
                      setCategories({ 'Thạch (Jelly)': 0, 'Kẹo dẻo (Gummy)': 0, 'Kẹo xốp (Marshmallow)': 0, 'Bánh quy (Biscuit)': 0, 'Bánh kem (Cake)': 0 });
                      setShowResetDialog(false);
                    });
                  }}
                  className="flex-1 py-3 rounded-xl bg-red-500 hover:bg-red-400 disabled:bg-red-500/20 disabled:text-red-500/40 disabled:cursor-not-allowed text-white font-black text-sm transition-all"
                >
                  XOÁ DỮ LIỆU
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
