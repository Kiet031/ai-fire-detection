import cv2
import numpy as np

# Hỗ trợ cả TensorFlow Lite bản đầy đủ (trên PC) và TFLite Runtime (trên Raspberry Pi)
try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    import tensorflow as tf
    Interpreter = tf.lite.Interpreter

class FireDetectorTFLite:
    # Đã hạ mức confidence mặc định xuống 0.25 cho mô hình nhỏ
    def __init__(self, model_path="best_float16.tflite", confidence_thres=0.25, iou_thres=0.45, use_sahi=True):
        # 1. Khởi tạo Interpreter của TFLite
        self.interpreter = Interpreter(model_path=model_path)
        self.interpreter.allocate_tensors()
        
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        
        self.conf_threshold = confidence_thres
        self.iou_threshold = iou_thres
        self.use_sahi = use_sahi  
        self.classes = ['smoke', 'fire']
        
        # Cấu hình kích thước mặc định cho Model mới
        self.model_size = 224
        
        # Lấy định dạng đầu vào (Shape) mà YOLOv8 đã export ra TFLite
        self.input_shape = self.input_details[0]['shape']
        self.is_nchw = (self.input_shape[1] == 3)
        
        # --- KỸ THUẬT: MOTION-TRIGGERED ---
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)

    def preprocess(self, image):
        """ Ép ảnh về 224x224 và định dạng ma trận cho TFLite """
        img = cv2.resize(image, (self.model_size, self.model_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        
        # Xử lý tự động theo chuẩn ma trận mà TFLite yêu cầu
        if self.is_nchw:
            img = np.transpose(img, (2, 0, 1)) # Chuyển thành CHW
            
        img = np.expand_dims(img, axis=0) # Thêm Batch size -> [1, ...]
        return img

    def get_motion_slices(self, frame, min_area=100):
        """
        KỸ THUẬT: Patch-based + Motion Trigger (Đã hạ xuống 224x224)
        """
        original_h, original_w = frame.shape[:2]
        
        # Luôn giữ 1 frame toàn cảnh
        slices = [(0, 0, original_w, original_h)]
        
        if not self.use_sahi:
            return slices

        fg_mask = self.bg_subtractor.apply(frame)
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        for contour in contours:
            # min_area = 100 vì khung ngắm 224x224 sẽ nhạy bén hơn rất nhiều
            if cv2.contourArea(contour) > min_area:
                x, y, w, h = cv2.boundingRect(contour)
                
                cx, cy = x + w // 2, y + h // 2
                x1 = max(0, cx - self.model_size // 2)
                y1 = max(0, cy - self.model_size // 2)
                x2 = min(original_w, x1 + self.model_size)
                y2 = min(original_h, y1 + self.model_size)
                
                if x2 - x1 < self.model_size: x1 = max(0, x2 - self.model_size)
                if y2 - y1 < self.model_size: y1 = max(0, y2 - self.model_size)
                
                slices.append((x1, y1, x2, y2))
                
        # Vẫn giới hạn 3 cửa sổ trượt để Pi quét chớp nhoáng
        return list(set(slices))[:3]

    def detect(self, frame):
        original_h, original_w = frame.shape[:2]
        
        if self.use_sahi:
            slices = self.get_motion_slices(frame)
        else:
            slices = [(0, 0, original_w, original_h)]
            
        all_boxes = []
        all_scores = []
        all_class_ids = []

        for (x1, y1, x2, y2) in slices:
            slice_img = frame[y1:y2, x1:x2]
            slice_h, slice_w = slice_img.shape[:2]
            
            input_tensor = self.preprocess(slice_img)
            
            # Đưa dữ liệu vào TFLite và chạy (Invoke)
            self.interpreter.set_tensor(self.input_details[0]['index'], input_tensor)
            self.interpreter.invoke()
            outputs = self.interpreter.get_tensor(self.output_details[0]['index'])
            
            # --- ĐÃ SỬA LỖI TỰ ĐỘNG ĐẢO CHIỀU MA TRẬN ---
            predictions = np.squeeze(outputs)
            
            # YOLOv8 TFLite có thể xuất ra 2 dạng: [Số Box, Thông số] hoặc [Thông số, Số Box]
            # Ta kiểm tra: Nếu số cột > số hàng -> Mới lật ma trận
            if len(predictions.shape) == 2 and predictions.shape[0] < predictions.shape[1]:
                predictions = predictions.T
            # --------------------------------------------
            
            # Tỷ lệ scale bây giờ là chia cho 224
            x_factor = slice_w / self.model_size
            y_factor = slice_h / self.model_size

            # --- PRE-FILTERING ---
            max_scores = np.amax(predictions[:, 4:], axis=1)
            valid_indices = np.where(max_scores > self.conf_threshold)[0]

            for idx in valid_indices:
                row = predictions[idx]
                classes_scores = row[4:]
                class_id = np.argmax(classes_scores)
                score = classes_scores[class_id]
                
                cx, cy, w, h = row[0], row[1], row[2], row[3]
                
                left = int((cx - w / 2) * x_factor)
                top = int((cy - h / 2) * y_factor)
                width = int(w * x_factor)
                height = int(h * y_factor)
                
                global_left = left + x1
                global_top = top + y1
                
                all_boxes.append([global_left, global_top, width, height])
                all_scores.append(float(score))
                all_class_ids.append(class_id)

        indices = cv2.dnn.NMSBoxes(all_boxes, all_scores, self.conf_threshold, self.iou_threshold)
        
        fire_detected = False
        fire_locations = []

        if len(indices) > 0:
            indices = indices.flatten() if hasattr(indices, 'flatten') else [i[0] if isinstance(i, (list, tuple, np.ndarray)) else i for i in indices]
            
            for i in indices:
                box = all_boxes[i]
                left, top, width, height = box
                class_id = all_class_ids[i]
                label = self.classes[class_id]
                
                if label == 'fire':
                    fire_detected = True
                    color = (0, 0, 255) # Đỏ
                    
                    center_x = left + (width // 2)
                    center_y = top + (height // 2)
                    # Góc bắn nước của ESP32 Servo
                    angle_offset = round(((center_x - (original_w / 2)) / original_w) * 105, 1)
                    
                    fire_locations.append({
                        "x": center_x, "y": center_y, "angle": angle_offset
                    })
                    
                    cv2.circle(frame, (center_x, center_y), 5, (0, 255, 255), -1)
                else:
                    color = (255, 0, 0) # Khói (Xanh)
                    
                cv2.rectangle(frame, (left, top), (left + width, top + height), color, 2)
                cv2.putText(frame, f"{label} {all_scores[i]:.2f}", (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        return frame, fire_detected, fire_locations