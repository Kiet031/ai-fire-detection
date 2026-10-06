import cv2
import numpy as np
import onnxruntime as ort

class FireDetectorONNX224:
    # Đổi mặc định confidence_thres xuống 0.25 cho mô hình nhỏ (224x224)
    def __init__(self, model_path="best_224.onnx", confidence_thres=0.2, iou_thres=0.45, use_sahi=True):
        self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        
        self.conf_threshold = confidence_thres
        self.iou_threshold = iou_thres
        self.use_sahi = use_sahi  
        self.classes = ['smoke', 'fire']
        
        # Cấu hình kích thước mặc định cho Model ONNX mới
        self.model_size = 224
        
        # --- KỸ THUẬT 1: MOTION-TRIGGERED ---
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)

    def preprocess(self, image):
        """ Ép ảnh về 224x224 chuẩn ONNX """
        img = cv2.resize(image, (self.model_size, self.model_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))
        img = np.expand_dims(img, axis=0)
        return img

    def get_motion_slices(self, frame, min_area=100):
        """
        KỸ THUẬT: Patch-based + Motion Trigger (Đã hạ xuống 224x224)
        """
        original_h, original_w = frame.shape[:2]
        
        # Luôn giữ 1 frame toàn cảnh để theo dõi tổng thể
        slices = [(0, 0, original_w, original_h)]
        
        if not self.use_sahi:
            return slices

        # Dùng MOG2 để tìm ra tọa độ chuyển động
        fg_mask = self.bg_subtractor.apply(frame)
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        for contour in contours:
            # Diện tích chuyển động hạ xuống 100 do khung ngắm 224x224 rất nhỏ và nhạy
            if cv2.contourArea(contour) > min_area:
                x, y, w, h = cv2.boundingRect(contour)
                
                cx, cy = x + w // 2, y + h // 2
                x1 = max(0, cx - self.model_size // 2)
                y1 = max(0, cy - self.model_size // 2)
                x2 = min(original_w, x1 + self.model_size)
                y2 = min(original_h, y1 + self.model_size)
                
                # Chống tràn viền ảnh
                if x2 - x1 < self.model_size: x1 = max(0, x2 - self.model_size)
                if y2 - y1 < self.model_size: y1 = max(0, y2 - self.model_size)
                
                slices.append((x1, y1, x2, y2))
                
        # Giới hạn 3 khung (1 toàn cảnh, 2 chuyển động)
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
            outputs = self.session.run(None, {self.input_name: input_tensor})
            predictions = np.squeeze(outputs[0]).T
            
            # --- Tỷ lệ nhân (Scale) đã đổi cho phù hợp mốc chia 224 ---
            x_factor = slice_w / self.model_size
            y_factor = slice_h / self.model_size

            # Lọc thô bằng Numpy tốc độ cao
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
                
                # Chuyển về tọa độ thực của ảnh Full HD
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