import os
import random
import qrcode
from PIL import Image, ImageDraw, ImageFont

# 1. Cấu hình dữ liệu nhà máy
CANDY_TYPES = {
    "Thạch (Jelly)": 50000,
    "Kẹo dẻo (Gummy)": 70000,
    "Kẹo xốp (Marshmallow)": 65000,
    "Bánh quy (Biscuit)": 90000,
    "Bánh kem (Cake)": 80000
}

QUANTITIES = [50, 100, 200, 500]

OUTPUT_DIR = "image-test"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def generate_qr(data):
    # Tăng box_size lên 12 để QR to và rõ hơn
    qr = qrcode.QRCode(version=1, box_size=12, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert('RGB')

def remove_accents(s):
    # Hàm xóa dấu tiếng Việt cơ bản để tránh lỗi hiển thị tiếng Trung trên một số máy quét
    import unicodedata
    import re
    s = unicodedata.normalize('NFD', s)
    s = s.encode('ascii', 'ignore').decode("utf-8")
    return s

def create_composite_image(index):
    # Tạo nền xám kim loại (Giả làm băng chuyền)
    width, height = 1280, 720
    bg = Image.new('RGB', (width, height), (60, 63, 65))
    draw = ImageDraw.Draw(bg)
    
    # Thử load font tiếng Việt (MacOS)
    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    try:
        font_large = ImageFont.truetype(font_path, 20)
        font_small = ImageFont.truetype(font_path, 16)
    except:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Vẽ các đường kẻ ngang giả làm con lăn băng chuyền
    for i in range(0, height, 80):
        draw.line([(0, i), (width, i)], fill=(45, 48, 50), width=5)
    
    # Số lượng thùng hàng trên băng chuyền (chỉnh lại 3-4 thùng để không quá chật)
    num_items = random.randint(3, 4)
    
    # Chia lưới 2x2 để các thùng to hơn
    cols, rows = 2, 2
    cell_w, cell_h = width // cols, height // rows
    spots = [(c, r) for c in range(cols) for r in range(rows)]
    random.shuffle(spots)
    
    for i in range(min(num_items, len(spots))):
        candy_name = random.choice(list(CANDY_TYPES.keys()))
        unit_price = CANDY_TYPES[candy_name]
        qty = random.choice(QUANTITIES)
        
        # Xóa dấu tiếng Việt CHỈ TRONG DỮ LIỆU QR để tránh hiện tiếng Trung
        # FACTORY|Name|Qty|Price
        # Xóa dấu tiếng Việt CHỈ TRONG DỮ LIỆU QR để tránh hiện tiếng Trung
        # FACTORY|UUID|Name|Qty|Price
        import uuid
        box_uuid = str(uuid.uuid4())[:8] # Lấy 8 ký tự đầu của UUID cho gọn
        safe_name = remove_accents(candy_name)
        qr_data = f"FACTORY|{box_uuid}|{safe_name}|{qty}|{unit_price}"
        qr_img = generate_qr(qr_data)
        
        # Đảm bảo box_size luôn nhỏ hơn cell_h và cell_w để không lỗi randint
        box_size = random.randint(280, 320) 
        qr_img = qr_img.resize((box_size - 60, box_size - 60))
        
        # Vị trí ngẫu nhiên trong ô lưới (Grid) với khoảng đệm an toàn
        c, r = spots[i]
        x_limit = max(20, cell_w - box_size - 20)
        y_limit = max(20, cell_h - box_size - 20)
        
        x_base = c * cell_w + random.randint(10, x_limit)
        y_base = r * cell_h + random.randint(10, y_limit)
        
        # Vẽ hình chữ nhật giả làm Thùng hàng (Brownish color)
        box_color = (205, 170, 125) # Màu bìa carton
        draw.rectangle([x_base, y_base, x_base + box_size, y_base + box_size], fill=box_color, outline=(139, 115, 85), width=3)
        
        # Dán QR vào giữa thùng
        bg.paste(qr_img, (x_base + 30, y_base + 30))
        
        # Viết nhãn thông tin TIẾNG VIỆT CÓ DẤU phía trên thùng (dùng font Arial)
        label_text = f"{candy_name}"
        sub_label = f"Qty: {qty}"
        draw.text((x_base + 5, y_base - 45), label_text, fill=(255, 255, 255), font=font_large)
        draw.text((x_base + 5, y_base - 20), sub_label, fill=(200, 200, 200), font=font_small)

    file_path = os.path.join(OUTPUT_DIR, f"conveyor_test_{index+1}.jpg")
    bg.save(file_path, quality=95)
    print(f"Generated: {file_path}")

if __name__ == "__main__":
    print("🏭 Đang tạo dữ liệu Băng chuyền Nhà máy Bánh kẹo...")
    # Cần cài đặt thư viện nếu chưa có: pip install qrcode Pillow
    for i in range(10): # Tạo 10 ảnh mẫu
        create_composite_image(i)
    print(f"✅ Xong! Toàn bộ 10 ảnh test nằm trong: {OUTPUT_DIR}")
