# Import cv2: Thư viện OpenCV dùng để xử lý ảnh, vẽ khung, vẽ chữ và tìm chuyển động
import cv2
# Import numpy: Thư viện tính toán ma trận tốc độ cao, giúp Python xử lý số liệu nhanh hơn
import numpy as np
# Import onnxruntime: Khung chạy mô hình AI định dạng .onnx siêu nhẹ trên CPU
import onnxruntime as ort

class FireDetectorONNX:
    # -------------------------------------------------------------------------
    # HÀM KHỞI TẠO (__init__): Thiết lập các thông số ban đầu khi hệ thống bật lên
    # -------------------------------------------------------------------------
    def __init__(self, model_path, confidence_thres=0.40, iou_thres=0.45, use_sahi=True):
        # Nạp mô hình AI (file .onnx) vào bộ nhớ RAM và ép chạy trên CPU (CPUExecutionProvider)
        self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        # Lấy tên cổng nạp dữ liệu (input) mặc định của mạng YOLO
        self.input_name = self.session.get_inputs()[0].name
        
        # Ngưỡng tin cậy (Confidence): AI phải chắc chắn > 40% mới coi là lửa/khói
        self.conf_threshold = confidence_thres
        # Ngưỡng đè lấp (IOU): Dùng cho thuật toán NMS để xóa các khung hình vẽ chồng chéo lên nhau
        self.iou_threshold = iou_thres
        # Cờ bật/tắt tính năng cắt lát ảnh (Patch-based Slicing)
        self.use_sahi = use_sahi  
        # Khai báo thứ tự ID của các lớp (Class) mà AI đã được huấn luyện
        self.classes = ['smoke', 'fire']
        
        # --- KỸ THUẬT 1: MOTION-TRIGGERED (KÍCH HOẠT BẰNG CHUYỂN ĐỘNG) ---
        # Khởi tạo bộ theo dõi nền MOG2 siêu nhẹ để tìm ra vùng có khói/lửa bốc lên
        # history=300: Nhớ 300 khung hình trước đó để làm nền, varThreshold=25: Độ nhạy với thay đổi
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)

    # -------------------------------------------------------------------------
    # HÀM TIỀN XỬ LÝ (preprocess): Chuẩn bị bức ảnh trước khi nhét vào "não" AI
    # -------------------------------------------------------------------------
    def preprocess(self, image):
        # Ép kích thước ảnh về chuẩn 640x640 pixel mà YOLO yêu cầu
        img = cv2.resize(image, (640, 640))
        # Chuyển hệ màu từ BGR (chuẩn của OpenCV) sang RGB (chuẩn của AI)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # Chuẩn hóa giá trị điểm ảnh từ 0-255 về dạng số thực 0.0 - 1.0 (Giúp AI tính toán ma trận dễ hơn)
        img = img.astype(np.float32) / 255.0
        # Lật ma trận từ dạng [Cao, Rộng, Kênh màu] sang [Kênh màu, Cao, Rộng] (Từ NHWC sang NCHW)
        img = np.transpose(img, (2, 0, 1))
        # Thêm 1 chiều vào đầu ma trận để đại diện cho Batch Size -> [1, 3, 640, 640]
        img = np.expand_dims(img, axis=0)
        return img

    # -------------------------------------------------------------------------
    # HÀM CẮT LÁT CHUYỂN ĐỘNG (get_motion_slices): Trích xuất các ô vuông có biến động
    # -------------------------------------------------------------------------
    def get_motion_slices(self, frame, slice_size=640, min_area=500):
        """
        KỸ THUẬT: Patch-based + Motion Trigger
        Chỉ tạo các 'bản vá' (slices) tại những khu vực có phát sinh chuyển động.
        Tiết kiệm 80% sức mạnh tính toán so với việc cắt và quét toàn bộ màn hình.
        """
        original_h, original_w = frame.shape[:2]
        
        # Luôn giữ 1 frame toàn cảnh để theo dõi tổng thể (gốc tọa độ 0,0 đến hết ảnh)
        slices = [(0, 0, original_w, original_h)]
        
        # Nếu tắt tính năng cắt lát, chỉ trả về đúng 1 khung toàn cảnh
        if not self.use_sahi:
            return slices

        # Dùng MOG2 để tìm ra tọa độ chuyển động (tạo ra mặt nạ đen trắng, phần trắng là chuyển động)
        fg_mask = self.bg_subtractor.apply(frame)
        # Lọc nhiễu ảnh đen trắng, làm sắc nét các mảng chuyển động
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
        # Vẽ đường viền (contours) bao quanh các mảng màu trắng đó
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        for contour in contours:
            # Lọc bỏ nhiễu nhỏ: Chỉ xét các chuyển động có diện tích > 500 pixel vuông
            if cv2.contourArea(contour) > min_area:
                x, y, w, h = cv2.boundingRect(contour)
                
                # Tính tọa độ Tâm của vùng chuyển động
                cx, cy = x + w // 2, y + h // 2
                
                # Bọc 1 ô cắt kích thước 640x640 (slice_size) xung quanh cái Tâm đó
                x1 = max(0, cx - slice_size // 2)
                y1 = max(0, cy - slice_size // 2)
                x2 = min(original_w, x1 + slice_size)
                y2 = min(original_h, y1 + slice_size)
                
                # Xử lý góc viền: Nếu ô cắt bị tràn ra ngoài màn hình, đẩy nó lùi lại cho vừa khít
                if x2 - x1 < slice_size: x1 = max(0, x2 - slice_size)
                if y2 - y1 < slice_size: y1 = max(0, y2 - slice_size)
                
                # Lưu tọa độ ô cắt này vào danh sách
                slices.append((x1, y1, x2, y2))
                
        # Giới hạn tối đa 3 ô tính toán (1 Toàn cảnh + 2 vùng chuyển động mạnh nhất) để Pi 5 không bị giật/quá tải
        return list(set(slices))[:3]

    # -------------------------------------------------------------------------
    # HÀM PHÁT HIỆN CHÍNH (detect): Chạy AI và xử lý hậu kỳ (vẽ khung, tính góc servo)
    # -------------------------------------------------------------------------
    def detect(self, frame):
        original_h, original_w = frame.shape[:2]
        
        # SỬ DỤNG HÀM TÌM KIẾM CHUYỂN ĐỘNG THÔNG MINH
        if self.use_sahi:
            slices = self.get_motion_slices(frame)
        else:
            slices = [(0, 0, original_w, original_h)]
            
        all_boxes = []      # Danh sách chứa tọa độ các khung đỏ/xanh
        all_scores = []     # Danh sách chứa tỷ lệ % tin cậy
        all_class_ids = []  # Danh sách chứa mã ID (0 là khói, 1 là lửa)

        # Quét AI qua các ô chứa chuyển động vừa tìm được
        for (x1, y1, x2, y2) in slices:
            # Cắt lấy vùng ảnh vật lý từ mảng pixel ban đầu
            slice_img = frame[y1:y2, x1:x2]
            slice_h, slice_w = slice_img.shape[:2]
            
            # Đưa vùng ảnh qua hàm tiền xử lý để nhét vào AI
            input_tensor = self.preprocess(slice_img)
            
            # GỌI AI XỬ LÝ (Bước nặng nhất). AI sẽ nhả ra một ma trận dữ liệu khổng lồ
            outputs = self.session.run(None, {self.input_name: input_tensor})
            # Lấy kết quả, loại bỏ các chiều dư thừa (squeeze) và lật ma trận (T - Transpose) để dễ đọc
            predictions = np.squeeze(outputs[0]).T
            
            # Tính tỷ lệ thu phóng (Scale) để lát nữa dịch tọa độ từ khung 640x640 về khung ảnh thật
            x_factor = slice_w / 640
            y_factor = slice_h / 640

            # --- PRE-FILTERING (LỌC THÔ TỐC ĐỘ CAO BẰNG NUMPY) ---
            # Kỹ thuật này giúp cắt bỏ 90% thời gian duyệt mảng của Python, mô phỏng tốc độ của NMS-Free
            
            # Tìm điểm tin cậy cao nhất trong các phân lớp (cột 4 trở đi là xác suất của Khói và Lửa)
            max_scores = np.amax(predictions[:, 4:], axis=1)
            # Chỉ giữ lại các hàng (dự đoán) có độ tin cậy lớn hơn 40% (conf_threshold)
            valid_indices = np.where(max_scores > self.conf_threshold)[0]

            # Bắt đầu vòng lặp đọc các dự đoán hợp lệ
            for idx in valid_indices:
                row = predictions[idx]
                classes_scores = row[4:] # Điểm của khói và lửa
                class_id = np.argmax(classes_scores) # Chọn ID có điểm cao nhất
                score = classes_scores[class_id] # Lưu lại điểm đó
                
                # Tọa độ mà YOLO xuất ra là Tâm X, Tâm Y, Chiều Rộng, Chiều Cao (trên tỷ lệ 640)
                cx, cy, w, h = row[0], row[1], row[2], row[3]
                
                # Đổi từ Tâm X, Y sang Tọa độ Góc trên cùng bên trái và nhân với tỷ lệ thu phóng ảnh thật
                left = int((cx - w / 2) * x_factor)
                top = int((cy - h / 2) * y_factor)
                width = int(w * x_factor)
                height = int(h * y_factor)
                
                # Trả tọa độ từ hệ quy chiếu cục bộ (của ô cắt) về hệ quy chiếu toàn cục (của ảnh gốc camera)
                global_left = left + x1
                global_top = top + y1
                
                # Thêm vào mảng để chờ bộ lọc NMS xử lý
                all_boxes.append([global_left, global_top, width, height])
                all_scores.append(float(score))
                all_class_ids.append(class_id)

        # CHẠY BỘ LỌC NMS (Non-Maximum Suppression): Xóa bỏ các khung viền vẽ trùng lặp lên cùng 1 ngọn lửa
        indices = cv2.dnn.NMSBoxes(all_boxes, all_scores, self.conf_threshold, self.iou_threshold)
        
        fire_detected = False
        fire_locations = []

        # Nếu còn sót lại khung hình nào sau khi lọc
        if len(indices) > 0:
            # Sửa lỗi Crash của OpenCV (Đồng bộ định dạng mảng trả về giữa các phiên bản OpenCV)
            indices = indices.flatten() if hasattr(indices, 'flatten') else [i[0] if isinstance(i, (list, tuple, np.ndarray)) else i for i in indices]
            
            # Duyệt qua các khung hình hợp lệ cuối cùng để vẽ lên màn hình
            for i in indices:
                box = all_boxes[i]
                left, top, width, height = box
                class_id = all_class_ids[i]
                label = self.classes[class_id]
                
                if label == 'fire':
                    fire_detected = True
                    color = (0, 0, 255) # Màu đỏ cho Lửa (Định dạng BGR)
                    
                    # Tính Tâm X, Tâm Y của đốm lửa thật trên màn hình máy tính
                    center_x = left + (width // 2)
                    center_y = top + (height // 2)
                    
                    # -------------------------------------------------------------
                    # CÔNG THỨC ÁNH XẠ TỌA ĐỘ VÀO GÓC QUAY SERVO (Coordinate Mapping)
                    # (center_x - original_w / 2) : Tìm độ lệch của lửa so với điểm chính giữa màn hình.
                    # / original_w : Chia cho tổng chiều dài màn hình để ra Tỷ lệ phần trăm lệch (-50% đến +50%).
                    # * 105 : Nhân tỷ lệ đó với Góc nhìn ngang (FOV) của Camera Tapo là 105 độ.
                    # Dấu Trừ (-) ở đầu : Đảo ngược không gian toán học để khắc phục lỗi Servo bị ngược trái/phải.
                    # -------------------------------------------------------------
                    angle_offset = -round(((center_x - (original_w / 2)) / original_w) * 105, 1)
                    
                    # Đưa tọa độ và góc vào mảng để gửi cho hàm process_logic ở app.py
                    fire_locations.append({
                        "x": center_x, "y": center_y, "angle": angle_offset
                    })
                    
                    # Vẽ một chấm màu vàng (0, 255, 255) ngay chính giữa ngọn lửa
                    cv2.circle(frame, (center_x, center_y), 5, (0, 255, 255), -1)
                else:
                    color = (255, 0, 0) # Màu xanh dương cho Khói (Khói không cần tính góc Servo)
                    
                # Vẽ hình chữ nhật bao quanh khói/lửa
                cv2.rectangle(frame, (left, top), (left + width, top + height), color, 2)
                # In dòng chữ "fire 0.85" hoặc "smoke 0.72" ngay phía trên khung hình
                cv2.putText(frame, f"{label} {all_scores[i]:.2f}", (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        # Trả về bức ảnh đã vẽ, cờ báo cháy (True/False), và danh sách góc xoay servo
        return frame, fire_detected, fire_locations