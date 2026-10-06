# =====================================================================
# THƯ VIỆN SỬ DỤNG
# =====================================================================
from flask import Flask, render_template, Response, request, redirect, url_for, jsonify # Thư viện tạo Web Server và API
import cv2            # Thư viện OpenCV xử lý hình ảnh, video và stream
import time           # Thư viện xử lý thời gian (đếm giây, tạo trễ)
import os             # Thư viện giao tiếp với hệ điều hành (tạo thư mục, đường dẫn)
import json           # Thư viện đọc/ghi file cấu hình định dạng JSON
import math           # Thư viện toán học (tính khoảng cách giữa 2 điểm)
from datetime import datetime # Thư viện lấy ngày giờ thực tế để in ra Log và tên ảnh
from werkzeug.utils import secure_filename # Thư viện bảo mật tên file khi người dùng upload
from detector import FireDetectorONNX      # Class AI nhận diện bản gốc (640x640)
from detector_416 import FireDetectorONNX416 # Class AI nhận diện bản thu nhỏ (416x416)
from hardware import ESP32Controller       # Class điều khiển giao tiếp I2C với ESP32

# Thiết lập biến môi trường cho OpenCV để đọc luồng RTSP từ camera mượt hơn qua giao thức TCP
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# =====================================================================
# CÁC BIẾN CẤU HÌNH HỆ THỐNG (HẰNG SỐ)
# =====================================================================
CONFIG_FILE = 'cameras.json'             # File lưu trữ thông tin (IP, User, Pass) của các camera
CONFIRM_HITS = 8                         # Số khung hình liên tiếp phải có lửa để xác nhận cháy thật (Chống nhiễu)
MAX_DIST = 80                            # Khoảng cách tối đa (pixel) để hệ thống nhận diện đây vẫn là cùng 1 ngọn lửa cũ
ALARM_AUTO_OFF_SECONDS = 30              # Thời gian (giây) đếm ngược để tự động tắt còi sau khi hết cháy
LOG_COOLDOWN_SECONDS = 10                # Thời gian "nghỉ" (giây) giữa 2 lần ghi log để tránh spam đầy màn hình
MAX_LOG_ENTRIES = 30                     # Số dòng Log tối đa hiển thị trên giao diện Web
UPLOAD_FOLDER = 'static/uploads'         # Thư mục lưu video/ảnh người dùng tải lên để Test
ALERTS_FOLDER = os.path.join(UPLOAD_FOLDER, 'alerts') # Thư mục con để lưu ảnh tự động chụp khi có cháy

# --- BIẾN CHUYỂN ĐỔI MÔ HÌNH AI (ĐỔI TRUE/FALSE ĐỂ SO SÁNH) ---
# Dùng để A/B Testing trực tiếp trên Raspberry Pi
USE_MODEL_416 = False  # True = ONNX (416x416) - Cân bằng, False = ONNX (640x640) - Tiêu chuẩn 
# ===============================================================

# Khởi tạo ứng dụng Web Flask
app = Flask(__name__)
# Tự động tạo các thư mục lưu trữ nếu chưa có
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ALERTS_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# =====================================================================
# KHỞI TẠO AI VÀ PHẦN CỨNG
# =====================================================================
# Logic tự động chọn mô hình AI tùy theo biến USE_MODEL_416
if USE_MODEL_416:
    ai_detector = FireDetectorONNX416(model_path="best_416.onnx", confidence_thres=0.30)
    print("🚀 Khởi động Hệ thống với mô hình: ONNX (416x416) - Cân bằng Hiệu năng")
else:
    ai_detector = FireDetectorONNX(model_path="best.onnx")
    print("🚀 Khởi động Hệ thống với mô hình: ONNX (640x640) - Tiêu chuẩn")

# Khởi tạo đối tượng điều khiển ESP32 qua I2C
hw = ESP32Controller()

# Hàm đọc file JSON để lấy danh sách Camera đã lưu lúc trước
def load_cameras():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

cameras = load_cameras()

# =====================================================================
# BIẾN TRẠNG THÁI TOÀN CỤC (LƯU TRỮ TÌNH TRẠNG HIỆN TẠI CỦA HỆ THỐNG)
# =====================================================================
system_status = {
    "cam_id": None,             # ID của camera đang được xem trên Web
    "alarm": False,             # Cờ trạng thái: True nếu đang có báo động cháy
    "has_captured": False,      # Cờ trạng thái: Đã chụp ảnh báo cháy lưu làm bằng chứng chưa
    "locations": [],            # Mảng chứa tọa độ và góc xoay của các đám cháy
    "logs": [],                 # Mảng chứa các dòng lịch sử (hiển thị trên giao diện)
    "last_log_time": 0,         # Thời điểm ghi log cuối cùng (để chặn spam log)
    "last_fire_time": 0,        # Thời điểm cuối cùng nhìn thấy ngọn lửa (dùng để đếm ngược tắt báo động)
    "last_alarm_sound_time": 0, # Biến kiểm soát vòng lặp 30s của loa (Yêu cầu 2)
    "last_servo_time": 0,       # Biến kiểm soát 3s mô phỏng phun nước (Yêu cầu 1)
    "servo_index": 0,           # Vị trí đám cháy trong mảng đang được ưu tiên phun
    "hw_test_mode": False,      # Chế độ tắt AI để Test điều khiển phần cứng bằng tay
    "candidates": []            # Mảng lưu tạm các đốm lửa đang trong quá trình theo dõi (chưa đủ điều kiện báo cháy)
}

# Hàm ép buộc hệ thống quay về trạng thái An toàn (Bấm nút Tắt Còi / Safe)
def force_dismiss():
    system_status["alarm"] = False
    system_status["candidates"] = []
    system_status["has_captured"] = False
    system_status["servo_index"] = 0

# =====================================================================
# HÀM XỬ LÝ LOGIC CHỐNG NHIỄU VÀ ĐIỀU KHIỂN PHẦN CỨNG
# =====================================================================
def process_logic(fire_detected, locations):
    current_time = time.time()
    real_fire_confirmed = False

    # 1. BỘ LỌC KHÔNG GIAN - THỜI GIAN (Spatial-Temporal Filter)
    if fire_detected:
        for loc in locations:
            matched = False
            # Quét xem ngọn lửa này có phải ngọn lửa cũ đã xuất hiện ở khung hình trước không
            for cand in system_status["candidates"]:
                # Tính khoảng cách Euclidean giữa tọa độ mới và tọa độ cũ
                dist = math.hypot(loc['x'] - cand['x'], loc['y'] - cand['y'])
                # Nếu lửa xê dịch ít (dưới MAX_DIST) -> Đây là lửa thật đang cháy tại chỗ
                if dist < MAX_DIST:
                    cand['x'] = loc['x']
                    cand['y'] = loc['y']
                    cand['angle'] = loc['angle']
                    cand['hits'] += 1 # Cộng dồn số khung hình xuất hiện (hits)
                    cand['last_time'] = current_time
                    matched = True
                    break

            # Nếu ngọn lửa này hoàn toàn mới (chưa có trong danh sách theo dõi) -> Thêm vào danh sách chờ
            if not matched:
                system_status["candidates"].append({
                    'x': loc['x'], 'y': loc['y'], 'angle': loc['angle'],
                    'hits': 1, 'last_time': current_time
                })

    # Dọn dẹp bộ nhớ: Xóa các "ứng viên" ngọn lửa chập chờn biến mất quá 1.5 giây
    system_status["candidates"] = [c for c in system_status["candidates"] if current_time - c['last_time'] < 1.5]

    # Lấy ra danh sách TOÀN BỘ các đám cháy đã được xác nhận (Phải tồn tại trên CONFIRM_HITS khung hình)
    confirmed_locs = [{'x': c['x'], 'y': c['y'], 'angle': c['angle']} for c in system_status["candidates"] if c['hits'] >= CONFIRM_HITS]
    
    if len(confirmed_locs) > 0:
        real_fire_confirmed = True

    # 2. XỬ LÝ TRẠNG THÁI KHI CÓ CHÁY THẬT
    if real_fire_confirmed:
        system_status["last_fire_time"] = current_time # Reset bộ đếm an toàn
        
        # ---------------------------------------------------------
        # YÊU CẦU 2: GỬI LẠI LỆNH LOA NẾU CHÁY KÉO DÀI QUÁ 30S
        # ---------------------------------------------------------
        if not system_status["alarm"]:
            # Lần đầu tiên phát hiện cháy
            system_status["alarm"] = True
            system_status["last_alarm_sound_time"] = current_time
            if not system_status.get("hw_test_mode", False):
                hw.trigger_alarm(1) # Hú còi bài số 1

            # Ghi Log lên web
            if current_time - system_status.get("last_log_time", 0) > LOG_COOLDOWN_SECONDS:
                log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] CẢNH BÁO: Đã xác nhận cháy! Bắt đầu phun nước."
                system_status["logs"].insert(0, log_msg)
                system_status["logs"] = system_status["logs"][:MAX_LOG_ENTRIES]
                system_status["last_log_time"] = current_time
        else:
            # Nếu ĐANG trong trạng thái báo cháy và đã trôi qua 30s -> Gửi lại lệnh ALARM:1
            if current_time - system_status.get("last_alarm_sound_time", 0) >= 30.0:
                if not system_status.get("hw_test_mode", False):
                    hw.trigger_alarm(1)
                system_status["last_alarm_sound_time"] = current_time

        # ---------------------------------------------------------
        # YÊU CẦU 1: XOAY VÒNG SERVO - MỖI GÓC PHUN 3 GIÂY
        # ---------------------------------------------------------
        # Nếu đã qua 3 giây từ lần bẻ góc gần nhất -> Bẻ sang góc của đốm lửa tiếp theo
        if current_time - system_status.get("last_servo_time", 0) >= 3.0:
            idx = system_status.get("servo_index", 0)
            
            # Nếu đã xịt đến đám cháy cuối cùng trong mảng, vòng lại ưu tiên đám cháy đầu tiên
            if idx >= len(confirmed_locs):
                idx = 0
                
            # Lấy góc quay của đám cháy hiện tại và gửi xuống ESP32
            target_angle = confirmed_locs[idx]['angle']
            if not system_status.get("hw_test_mode", False):
                hw.move_servo(target_angle)
            
            # Lưu lại thời gian xịt và trỏ index tới đám cháy tiếp theo cho đợt 3s sau
            system_status["last_servo_time"] = current_time
            system_status["servo_index"] = idx + 1

    # 3. XỬ LÝ TRẠNG THÁI KHI HẾT CHÁY (HOẶC CHỈ CÓ KHÓI)
    else:
        # Nếu hệ thống đang báo động mà ngọn lửa đã biến mất
        if system_status["alarm"]:
            # Đợi qua thời gian Cooldown (30 giây) để chắc chắn lửa đã tắt hẳn
            if current_time - system_status["last_fire_time"] > ALARM_AUTO_OFF_SECONDS:
                force_dismiss() # Đưa hệ thống về an toàn
                if not system_status.get("hw_test_mode", False):
                    hw.trigger_alarm(2) # Gửi lệnh loa số 2 (Thông báo an toàn)

    # Cập nhật danh sách tọa độ lên Web để hiển thị tọa độ góc quay
    system_status["locations"] = confirmed_locs

# =====================================================================
# HÀM XUẤT LUỒNG VIDEO (MJPEG STREAMING)
# =====================================================================
def generate_stream(cam_id):
    """
    Hàm này mở kết nối tới Camera, lấy từng khung hình, đưa vào AI xử lý
    sau đó nén thành ảnh JPEG và đẩy liên tục ra Frontend (Tạo thành Video).
    """
    url = cameras.get(cam_id, {}).get("url", "")
    cap = cv2.VideoCapture(url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Chỉ giữ 1 frame mới nhất để giảm độ trễ (Lag)
    
    system_status["cam_id"] = cam_id
    
    while system_status["cam_id"] == cam_id:
        # Bỏ qua 3 frame liên tiếp để đuổi kịp thời gian thực (Drop frames)
        for _ in range(3): cap.grab()
        
        success, frame = cap.read()
        if not success:
            # Nếu đứt luồng, tự động reconnect sau 1 giây
            time.sleep(1)
            cap = cv2.VideoCapture(url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            continue

        # Nếu đang ở chế độ Test phần cứng -> Không chạy AI để làm nhẹ Pi
        if system_status.get("hw_test_mode", False):
            cv2.putText(frame, "CHE DO TEST PHAN CUNG (AI OFF)", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)
        else:
            # BƯỚC NẶNG NHẤT: Đưa ảnh qua hàm detect của AI để vẽ khung và tính tọa độ
            frame, fire_detected, locations = ai_detector.detect(frame)
            # Truyền kết quả vào hàm logic để phân tích và điều khiển phần cứng
            process_logic(fire_detected, locations)
            
            # XỬ LÝ GIAO DIỆN / LƯU ẢNH TRÊN VIDEO
            if system_status["alarm"]:
                # Tính năng chụp ảnh tự động khi có cháy (Chỉ chụp 1 lần mỗi đợt)
                if not system_status.get("has_captured", False):
                    filename = f"fire_{int(time.time())}.jpg"
                    filepath = os.path.join(ALERTS_FOLDER, filename)
                    
                    # Dán nhãn thời gian vào ảnh trước khi lưu
                    capture_frame = frame.copy()
                    cv2.putText(capture_frame, f"CHAY LUC: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                    cv2.imwrite(filepath, capture_frame)
                    
                    system_status["has_captured"] = True # Khóa cờ lại
                    
                    # Ghi Log xác nhận đã lưu bằng chứng
                    log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 📸 Đã tự động chụp ảnh: alerts/{filename}"
                    system_status["logs"].insert(0, log_msg)
                    system_status["logs"] = system_status["logs"][:MAX_LOG_ENTRIES]

                # In cảnh báo lên màn hình Video
                cv2.putText(frame, "CANH BAO CHAY!", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            
            elif len(system_status["candidates"]) > 0:
                # Nếu thấy lửa nhưng chưa đủ số khung hình (đang đếm hit)
                max_hits = max([c['hits'] for c in system_status["candidates"]])
                if max_hits > 1:
                    cv2.putText(frame, f"DANG XAC MINH... {max_hits}/{CONFIRM_HITS}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 165, 255), 3)

        # Scale ảnh về HD (1024x576) cho nhẹ, nén thành JPEG chất lượng 70%
        frame = cv2.resize(frame, (1024, 576)) 
        ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ret:
            # Dùng cú pháp Multipart của MJPEG để đẩy ảnh liên tục ra HTTP response
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            
    # Giải phóng tài nguyên camera khi tắt luồng
    cap.release()

# =====================================================================
# CÁC API VÀ ĐƯỜNG DẪN CỦA WEB SERVER (FLASK ROUTES)
# =====================================================================

# 1. API gốc: Trả về file giao diện HTML
@app.route('/')
def index():
    return render_template('index.html', cameras=cameras)

# 2. API Video Feed: Nhận ID camera và trả về luồng stream MJPEG
@app.route('/video_feed/<cam_id>')
def video_feed(cam_id):
    return Response(generate_stream(cam_id), mimetype='multipart/x-mixed-replace; boundary=frame')

# 3. API Đồng bộ trạng thái: Frontend sẽ gọi API này mỗi 1 giây để lấy trạng thái hệ thống
@app.route('/api/status/<cam_id>')
def api_status(cam_id):
    # Chỉ trả về dữ liệu nếu ID Camera trùng khớp với luồng đang xử lý
    if system_status["cam_id"] == cam_id:
        return jsonify(system_status)
    return jsonify({"alarm": False, "locations": [], "logs": []})

# 4. API Tắt còi thủ công: Gọi khi người dùng bấm nút "TẮT CÒI" trên web
@app.route('/api/dismiss/<cam_id>', methods=['POST'])
def api_dismiss(cam_id):
    force_dismiss()
    if not system_status.get("hw_test_mode", False):
        hw.trigger_alarm(2)  # Gửi ngay lệnh loa báo an toàn
    return jsonify({"status": "success"})

# 5. API Lưu Camera: Ghi thông tin từ form thêm/sửa vào file cấu hình
@app.route('/save_camera', methods=['POST'])
def save_camera():
    cam_id = request.form.get('cam_id') 
    name = request.form.get('cam_name')
    mode = request.form.get('config_mode')
    
    # Xử lý ghép chuỗi tự động cho Camera Tapo
    if mode == 'auto':
        ip = request.form.get('cam_ip')
        user = request.form.get('cam_user')
        pwd = request.form.get('cam_pass')
        
        # Link RTSP mặc định của Tapo: luồng chính (stream1)
        url = f"rtsp://{user}:{pwd}@{ip}:554/stream1"
        cam_data = {
            "name": name, "mode": mode, "url": url, 
            "ip": ip, "user": user, "password": pwd,
            "quality": "hd"
        }
    else:
        # Nếu nhập tay Link RTSP
        url = request.form.get('cam_url_manual')
        cam_data = {"name": name, "mode": mode, "url": url}
        
    # Tạo ID mới nếu đây là Camera thêm mới (dựa vào thời gian Unix)
    if not cam_id:
        cam_id = f"cam_{int(time.time())}"
        
    # Cập nhật mảng và ghi đè xuống file json
    cameras[cam_id] = cam_data
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cameras, f, ensure_ascii=False)
    
    system_status["cam_id"] = cam_id
    # Reload lại trang chủ
    return redirect(url_for('index'))

# 6. API Xóa Camera: Xóa cấu hình theo ID
@app.route('/delete_camera/<cam_id>', methods=['POST'])
def delete_camera(cam_id):
    if cam_id in cameras:
        del cameras[cam_id]
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cameras, f, ensure_ascii=False)
    return redirect(url_for('index'))

# 7. API Thay đổi độ phân giải: Đổi URL RTSP giữa HD và SD để giảm lag mạng
@app.route('/api/camera/resolution/<cam_id>', methods=['POST'])
def change_resolution(cam_id):
    quality = request.form.get('quality')
    cam_info = cameras.get(cam_id)
    
    if not cam_info or cam_info.get('mode') != 'auto':
        return jsonify({"status": "error", "message": "Không khả dụng"})
        
    ip = cam_info.get('ip')
    user = cam_info.get('user')
    pwd = cam_info.get('password')
    
    # stream1 = Bản HD nét cao, stream2 = Bản SD mượt mà
    stream_path = "stream1" if quality == 'hd' else "stream2"
    cam_info['url'] = f"rtsp://{user}:{pwd}@{ip}:554/{stream_path}"
    cam_info['quality'] = quality
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cameras, f, ensure_ascii=False)
        
    # Gửi tín hiệu RELOAD để ngắt luồng hiện tại và thiết lập luồng có độ phân giải mới
    if system_status["cam_id"] == cam_id:
        system_status["cam_id"] = "RELOAD"
        
    return jsonify({"status": "success"})


# =====================================================================
# CÁC API PHÂN TÍCH TEST (HÌNH ẢNH/VIDEO) VÀ TEST PHẦN CỨNG TỪ WEB
# =====================================================================

# 8. API Upload File: Nhận file ảnh/video từ máy tính tải lên để AI test
@app.route('/upload_test', methods=['POST'])
def upload_test():
    if 'file' not in request.files: return jsonify({"error": "Không có file"})
    file = request.files['file']
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    ext = filename.rsplit('.', 1)[1].lower()

    # Tạm ngắt camera, chuyển sang mode test
    system_status["cam_id"] = "test"
    force_dismiss()
    system_status["logs"] = []

    # Xử lý FILE ẢNH: Phân tích 1 lần, vẽ khung và trả link ảnh đã vẽ ra Frontend
    if ext in ['jpg', 'jpeg', 'png']:
        img = cv2.imread(filepath)
        img, fire_detected, locs = ai_detector.detect(img)
        if fire_detected:
            system_status["alarm"] = True
            cv2.putText(img, "CHAY! (CHEDO TEST)", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)

        out_filename = "out_" + filename
        out_filepath = os.path.join(app.config['UPLOAD_FOLDER'], out_filename)
        cv2.imwrite(out_filepath, img)
        return jsonify({"type": "image", "url": f"/static/uploads/{out_filename}", "filename": filename})
        
    # Xử lý FILE VIDEO: Trả link để Frontend trỏ luồng stream vào API số 9
    elif ext in ['mp4', 'avi', 'mov']:
        return jsonify({"type": "video", "url": f"/stream_test/{filename}", "filename": filename})
    return jsonify({"error": "Định dạng không hỗ trợ.", "filename": filename})

# 9. API Stream Video Upload: Tương tự như xem camera live, nhưng đọc frame từ file MP4
@app.route('/stream_test/<filename>')
def stream_test(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    def generate():
        cap = cv2.VideoCapture(filepath)
        while cap.isOpened() and system_status["cam_id"] == "test":
            success, frame = cap.read()
            if not success: break 
            frame, fire_detected, locations = ai_detector.detect(frame)
            process_logic(fire_detected, locations)
            
            if system_status["alarm"]:
                cv2.putText(frame, "CANH BAO CHAY! (TEST)", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
            elif len(system_status["candidates"]) > 0:
                max_hits = max([c['hits'] for c in system_status["candidates"]])
                if max_hits > 1:
                    cv2.putText(frame, f"DANG XAC MINH... {max_hits}/{CONFIRM_HITS}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 165, 255), 3)

            frame = cv2.resize(frame, (1024, 576))
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ret: yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            time.sleep(0.02)  # Delay nhẹ để khớp FPS của video
        cap.release()
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# 10. API Bật/Tắt Test Phần Cứng: Tạm ngắt AI chạy ngầm để thao tác Servo mượt mà
@app.route('/api/toggle_hw_test', methods=['POST'])
def toggle_hw_test():
    system_status["hw_test_mode"] = not system_status.get("hw_test_mode", False)
    if system_status["hw_test_mode"]: force_dismiss()
    return jsonify({"status": "success", "hw_test_mode": system_status["hw_test_mode"]})

# 11. API Test Servo: Bắn lệnh MOVE:góc tùy chỉnh xuống ESP32
@app.route('/api/test_servo', methods=['POST'])
def test_servo():
    angle = request.form.get('angle')
    try:
        hw.move_servo(float(angle))
        return jsonify({"status": "success", "message": f"Đã gửi lệnh quay {angle} độ qua I2C"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# 12. API Test Âm Thanh: Bắn lệnh ALARM:số_bài_hát xuống DFPlayer qua ESP32
@app.route('/api/test_audio', methods=['POST'])
def test_audio():
    filename = request.form.get('filename')
    try:
        track_number = int(filename) 
        hw.trigger_alarm(track_number)
        return jsonify({"status": "success", "message": f"Đã gửi lệnh phát bài {track_number} qua I2C"})
    except ValueError:
        return jsonify({"status": "error", "message": "Lỗi định dạng"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ---------------------------------------------------------------------
# API MỚI: MÔ PHỎNG CẢNH BÁO TỪ NÚT BẤM (Giao diện màu đỏ/xanh)
# ---------------------------------------------------------------------
@app.route('/api/simulate_alarm', methods=['POST'])
def simulate_alarm():
    state = request.form.get('state')
    current_time = time.time()
    
    if state == 'danger':
        system_status["alarm"] = True
        system_status["last_fire_time"] = current_time 
        system_status["last_alarm_sound_time"] = current_time # Reset thời gian loa để đếm lại 30s
        hw.trigger_alarm(1) # Bắn ngay lệnh hú còi
            
        log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🚨 MÔ PHỎNG: Kích hoạt báo cháy thủ công!"
        system_status["logs"].insert(0, log_msg)
        system_status["logs"] = system_status["logs"][:MAX_LOG_ENTRIES]
        
    elif state == 'safe':
        force_dismiss() # Bắt buộc dập lửa trên màn hình web
        hw.trigger_alarm(2) # Bắn ngay lệnh còi an toàn
            
        log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] ✅ MÔ PHỎNG: Kích hoạt an toàn thủ công!"
        system_status["logs"].insert(0, log_msg)
        system_status["logs"] = system_status["logs"][:MAX_LOG_ENTRIES]
        
    return jsonify({"status": "success"})

# =====================================================================
# KHỞI ĐỘNG MÁY CHỦ
# =====================================================================
if __name__ == '__main__':
    # Chạy Web Server trên toàn mạng nội bộ (0.0.0.0), Cổng 5000, bật Multi-threading để đa nhiệm
    app.run(host='0.0.0.0', port=5000, threaded=True)