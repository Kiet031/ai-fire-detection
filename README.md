# AI Fire Control Center

Ứng dụng giám sát và cảnh báo cháy bằng thị giác máy tính, phục vụ bài tập lớn hệ thống nhúng. Hệ thống đọc video từ camera IP qua RTSP hoặc từ tệp tải lên, nhận diện **lửa (`fire`)** và **khói (`smoke`)**, hiển thị kết quả trên web và gửi lệnh điều khiển ESP32 qua I2C.

Phần Python được thiết kế để chạy trên Raspberry Pi; ESP32 nhận lệnh điều khiển servo và phát âm thanh qua DFPlayer. Repository chứa ứng dụng Python, giao diện web, các mô hình AI và hai sketch Arduino cho ESP32. Firmware tích hợp nhận I2C nằm trong `speaker/speaker.ino`; `servo/servo.ino` là chương trình thử gửi lệnh UART. Project chưa có bản vẽ mạch, dữ liệu huấn luyện hoặc mã huấn luyện/xuất mô hình.

Nội dung dưới đây được đối chiếu với mã nguồn, metadata mô hình và môi trường Python cục bộ. Các thông số phần cứng là cấu hình trong chương trình, chưa phải kết quả kiểm chứng bằng mạch thực tế.

## Mục lục

- [1. Chức năng](#1-chức-năng)
- [2. Kiến trúc](#2-kiến-trúc)
- [3. Cấu trúc project](#3-cấu-trúc-project)
- [4. Yêu cầu và cài đặt](#4-yêu-cầu-và-cài-đặt)
- [5. Khởi chạy và sử dụng](#5-khởi-chạy-và-sử-dụng)
- [6. Mô hình AI và tham số](#6-mô-hình-ai-và-tham-số)
- [7. Giao tiếp I2C và firmware ESP32](#7-giao-tiếp-i2c-và-firmware-esp32)
- [8. Các endpoint và cấu trúc dữ liệu](#8-các-endpoint-và-cấu-trúc-dữ-liệu)
- [9. Phạm vi và giới hạn hiện tại](#9-phạm-vi-và-giới-hạn-hiện-tại)
- [10. Chuẩn bị đưa lên GitHub](#10-chuẩn-bị-đưa-lên-github)
- [11. Quy trình kiểm tra hệ thống](#11-quy-trình-kiểm-tra-hệ-thống)
- [12. Xử lý lỗi thường gặp](#12-xử-lý-lỗi-thường-gặp)

## 1. Chức năng

- Thêm, sửa, xóa camera; lưu cấu hình cục bộ trong `cameras.json`.
- Tạo URL RTSP từ IP, tài khoản và mật khẩu camera Tapo hoặc nhập URL thủ công.
- Xem video MJPEG kèm khung nhận diện, nhãn lớp và độ tin cậy.
- Chuyển luồng HD/SD cho camera cấu hình tự động (`stream1`/`stream2`).
- Phóng to hình ảnh trên giao diện từ 1× đến 3× bằng CSS.
- Xác nhận lửa bằng bộ lọc theo vị trí và số lần phát hiện để giảm cảnh báo nhiễu.
- Hiển thị tọa độ lửa, góc điều khiển servo và lịch sử cảnh báo.
- Phát âm thanh cảnh báo trên trình duyệt và gửi lệnh báo cháy/an toàn đến ESP32.
- Luân phiên hướng servo đến các vị trí lửa đã xác nhận, mỗi lần cách nhau ít nhất 3 giây.
- Chụp một ảnh cảnh báo cho mỗi đợt báo cháy khi xem camera trực tiếp.
- Thử AI bằng ảnh/video tải lên; mô phỏng báo cháy/an toàn và thử servo, âm thanh từ web.
- Tạm dừng AI trong luồng camera trực tiếp để thử phần cứng.

Khói được vẽ trên ảnh nhưng **không trực tiếp kích hoạt báo cháy hoặc tính góc servo** trong logic hiện tại.

## 2. Kiến trúc

```text
Camera IP (RTSP) / Ảnh, video tải lên
                  |
                  v
          OpenCV đọc khung hình
                  |
                  v
      Detector AI: tiền xử lý -> suy luận -> NMS
                  |
                  v
         app.py: xác nhận và xử lý báo cháy
                  |
         +--------+---------+
         |                  |
         v                  v
 Flask + giao diện web    hardware.py -> I2C -> ESP32
 Video, trạng thái, log                         |
 Ảnh cảnh báo                            Servo / DFPlayer
```

Detector trả về ảnh đã vẽ, cờ phát hiện lửa và danh sách `{x, y, angle}`. Các detector sử dụng OpenCV MOG2 để tìm vùng chuyển động và cắt thêm vùng ảnh cho suy luận; số vùng xử lý được giới hạn tối đa 3. Tùy chọn `use_sahi` trong mã là cơ chế cắt vùng tự triển khai, không yêu cầu cài thư viện SAHI.

### Luồng xử lý camera

1. Flask nạp mô hình AI, khởi tạo I2C và đọc cấu hình camera khi khởi động.
2. Trình duyệt tự chọn camera đầu tiên nếu có cấu hình; yêu cầu `/video_feed/<cam_id>` bắt đầu vòng lặp xử lý.
3. OpenCV mở RTSP với tùy chọn TCP, đặt bộ đệm yêu cầu bằng 1 và bỏ qua 3 khung trước mỗi lần đọc để giảm trễ. Nếu đọc thất bại, chương trình chờ 1 giây rồi thử mở lại.
4. Detector resize ảnh, đổi BGR sang RGB, chuẩn hóa điểm ảnh về `[0, 1]`, suy luận và lọc các hộp bằng NMS.
5. `process_logic()` ghép các vị trí lửa vào ứng viên, xác nhận báo động và gửi lệnh điều khiển.
6. Ảnh đưa lên web được resize thành **1024×576**, mã hóa JPEG chất lượng **70**, truyền bằng MJPEG. Tọa độ phát hiện vẫn theo ảnh nguồn trước bước resize hiển thị.
7. JavaScript gọi API trạng thái mỗi **1 giây** để cập nhật cảnh báo, tọa độ, âm thanh và log.

Video tải lên có thêm `sleep(0.02)` sau mỗi khung; đây là khoảng nghỉ cố định, chưa đồng bộ theo FPS hoặc timestamp gốc của video.

## 3. Cấu trúc project

```text
.
├── app.py                  # Flask, quản lý camera, stream, logic báo cháy và API
├── hardware.py             # Bộ điều khiển ESP32 qua smbus2/I2C
├── detector.py             # FireDetectorONNX, tiền xử lý 640×640
├── detector_416.py         # FireDetectorONNX416, tiền xử lý 416×416
├── detector_224.py         # FireDetectorONNX224, tiền xử lý 224×224
├── detector_tflite.py      # FireDetectorTFLite, tiền xử lý 224×224
├── best.onnx               # Mô hình mặc định
├── best_416.onnx            # Mô hình cho detector 416
├── best_224.onnx            # Mô hình cho detector 224
├── best_float16.tflite      # Mô hình TFLite mặc định của detector TFLite
├── best_int8.tflite         # Biến thể TFLite INT8 để thử nghiệm
├── speaker/
│   └── speaker.ino         # Firmware ESP32 nhận I2C, điều khiển servo và DFPlayer
├── servo/
│   └── servo.ino           # Sketch thử gửi ALARM:1 qua UART2
├── templates/
│   └── index.html          # Giao diện tiếng Việt, CSS và JavaScript
├── static/
│   └── uploads/            # Tệp tải lên và kết quả xử lý; tự tạo, không đưa lên Git
│       └── alerts/         # Ảnh cảnh báo từ camera trực tiếp
├── cameras.json            # Cấu hình camera cục bộ, không đưa lên Git
├── test_tapo.py             # Script thử pytapo cục bộ, không đưa lên Git
├── .gitignore
└── README.md
```

`env/` là môi trường Python cục bộ và không được đưa lên GitHub. Các mô hình `.onnx` và `.tflite` được giữ trong repository để người clone có thể chạy ứng dụng.

## 4. Yêu cầu và cài đặt

### Phần mềm

- Python 3 và `pip`; môi trường cục bộ của project được tạo bằng Python 3.13.5. Khả năng cài thư viện còn phụ thuộc hệ điều hành và kiến trúc CPU.
- Thư viện chính: Flask, OpenCV, NumPy, ONNX Runtime và smbus2.
- Camera có luồng RTSP mà OpenCV đọc được; camera và máy chạy ứng dụng cần kết nối mạng với nhau.
- Trình duyệt hỗ trợ JavaScript để xem giao diện và nhận cập nhật trạng thái.

Từ thư mục project, tạo môi trường ảo và cài các thư viện cho cấu hình ONNX mặc định:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install flask opencv-python numpy onnxruntime smbus2
```

Trên Windows, kích hoạt môi trường bằng `.venv\Scripts\activate` trong Command Prompt. Phần điều khiển I2C cần môi trường có thiết bị SMBus tương ứng; Windows không phải môi trường triển khai phần cứng của project.

Project chưa có `requirements.txt` hoặc khóa phiên bản thư viện. Lệnh trên liệt kê các phụ thuộc từ mã nguồn, không bảo đảm tái tạo chính xác môi trường cục bộ.

### Phiên bản ghi nhận trong môi trường cục bộ

Các phiên bản sau được đọc từ metadata gói trong `env/`, không phải yêu cầu phiên bản tối thiểu hoặc cam kết tương thích trên mọi máy:

| Thành phần | Phiên bản cục bộ | Vai trò |
| --- | --- | --- |
| Python | 3.13.5 | Môi trường ảo ban đầu |
| Flask | 3.1.3 | Web server và API |
| opencv-python | 4.13.0.92 | Đọc video, xử lý ảnh và MJPEG |
| NumPy | 2.4.6 | Tiền xử lý tensor và lọc dự đoán |
| ONNX Runtime | 1.27.0 | Chạy các mô hình ONNX |
| smbus2 | 0.6.1 | Gửi lệnh I2C |
| TensorFlow | 2.21.0 | Nhánh TFLite tùy chọn |
| pytapo | 3.4.15 | Script thử điều khiển camera riêng |

Werkzeug và Jinja2 được dùng qua Flask cho tên tệp upload và template HTML. Không cần cài Ultralytics để chạy nhánh ONNX hiện tại.

Ứng dụng chưa đọc `.env`; thay đổi đường dẫn, ngưỡng hoặc model được thực hiện trong mã Python. Việc `.gitignore` loại `.env` không có nghĩa project đã tích hợp nạp biến môi trường từ tệp này.

### Phần cứng

Để sử dụng điều khiển thực tế, cần Raspberry Pi có I2C, ESP32 chạy `speaker/speaker.ino`, servo, DFPlayer Mini/loa, thẻ nhớ chứa âm thanh và nguồn phù hợp. Trong `hardware.py`, cấu hình mặc định là:

```python
ESP32Controller(i2c_bus=1, i2c_address=0x08)
```

Bật I2C trên Raspberry Pi bằng `sudo raspi-config` → **Interface Options** → **I2C**. Bus và địa chỉ phải khớp với firmware ESP32. Cấu hình chân và cách nạp chương trình được trình bày ở mục 7.

Nếu không mở được I2C, chương trình đặt `bus = None` và ghi thông báo lỗi; phần nhận diện và giao diện vẫn có thể dùng khi các thư viện và mô hình đã được nạp thành công. Không có giao tiếp phần cứng giả lập thành công trong trường hợp này.

## 5. Khởi chạy và sử dụng

Chạy từ thư mục gốc project vì các đường dẫn mô hình và cấu hình là đường dẫn tương đối:

```bash
python app.py
```

Truy cập `http://localhost:5000` trên máy chạy ứng dụng hoặc `http://<IP-may-chay>:5000` từ thiết bị cùng mạng. Flask lắng nghe tại `0.0.0.0:5000` và bật `threaded=True`.

### Camera trực tiếp

1. Trong **Thêm Camera Live Mới**, nhập tên khu vực.
2. Chọn **Tự động (Tapo C200)** và nhập IP, tài khoản, mật khẩu RTSP; hoặc chọn **Nhập URL Thủ công**.
3. Nhấn **Lưu Hệ Thống** rồi chọn camera trong danh sách.
4. Theo dõi khung nhận diện, trạng thái, tọa độ và log. Dùng **TẮT CÒI** để xóa trạng thái báo động và gửi lệnh âm thanh an toàn khi không ở chế độ test phần cứng.

URL tự động có dạng sau; hãy thay các giá trị minh họa bằng cấu hình cục bộ:

```text
rtsp://CAMERA_USER:CAMERA_PASSWORD@CAMERA_IP:554/stream1
```

Không cần tạo sẵn `cameras.json`: khi chưa có tệp, danh sách camera rỗng và thao tác lưu camera sẽ tạo tệp. Tài khoản RTSP cần được cấu hình trên camera trước khi kết nối.

### Kiểm tra bằng ảnh/video

Trong **Phân Tích File (Test AI)**, chọn tệp và nhấn **Chạy Test**:

- Ảnh hỗ trợ: `.jpg`, `.jpeg`, `.png`. AI xử lý một lần và lưu ảnh kết quả với tiền tố `out_`. Phát hiện lửa sẽ đặt trạng thái báo động ngay, không qua bộ lọc xác nhận nhiều lần và không gọi logic servo tự động.
- Video hỗ trợ: `.mp4`, `.avi`, `.mov`. Hệ thống đọc từng khung hình và chạy logic xác nhận/báo cháy như camera. Việc giải mã còn phụ thuộc codec mà OpenCV hỗ trợ.
- Test video có thể gửi lệnh phần cứng; nút tạm tắt AI chỉ bỏ qua AI trong luồng camera trực tiếp, không bỏ qua suy luận trong luồng video test.
- Tệp và kết quả được lưu trong `static/uploads/`. Video mẫu hiện có trong thư mục cục bộ không được đưa lên GitHub.

### Kiểm tra phần cứng và mô phỏng

- Chọn camera để giao diện có luồng cập nhật trạng thái, sau đó nhấn **TẠM TẮT AI - ĐỂ TEST CỨNG** khi thử điều khiển bằng tay.
- Nhập góc và nhấn **Quay** để gửi `MOVE:<góc>`.
- Nhập số bài âm thanh, ví dụ `001`, và nhấn **Phát** để gửi `ALARM:1` sau khi chuyển thành số nguyên.
- Nhấn **BÁO CHÁY** hoặc **AN TOÀN** để đổi trạng thái và gửi lệnh âm thanh tương ứng. Hai nút mô phỏng vẫn gửi lệnh trong chế độ test phần cứng.
- Nhấn **BẬT LẠI AI** để trở lại nhận diện camera.

## 6. Mô hình AI và tham số

| Detector | Mô hình | Tiền xử lý | Ngưỡng tin cậy mặc định | Tích hợp trong `app.py` |
| --- | --- | --- | --- | --- |
| `FireDetectorONNX` | `best.onnx` | 640×640 | 0.40 | Đang dùng mặc định |
| `FireDetectorONNX416` | `best_416.onnx` | 416×416 | 0.30 | Chọn qua `USE_MODEL_416` |
| `FireDetectorONNX224` | `best_224.onnx` | 224×224 | 0.20 | Chưa được nối vào ứng dụng |
| `FireDetectorTFLite` | `best_float16.tflite` | 224×224 | 0.25 | Chưa được nối vào ứng dụng |

Các detector có ngưỡng IoU mặc định `0.45` và thứ tự lớp `['smoke', 'fire']`. ONNX chạy bằng `CPUExecutionProvider`.

Để chuyển sang bản 416, sửa trong `app.py` rồi khởi động lại:

```python
USE_MODEL_416 = True
```

Bản 224 và TFLite cần thay phần import/khởi tạo detector trong `app.py`. Detector TFLite ưu tiên `tflite_runtime`, sau đó dùng TensorFlow nếu không import được runtime; cần cài một trong hai khi thử nhánh này. `best_int8.tflite` chưa được chọn trong ứng dụng và có kích thước đầu vào khác với detector TFLite hiện tại, nên không thể chỉ thay tên tệp là dùng được.

### Logic xác nhận và điều khiển

| Tham số trong `app.py` | Giá trị | Ý nghĩa |
| --- | --- | --- |
| `CONFIRM_HITS` | 8 | Số lần phát hiện khớp cần tích lũy để xác nhận một ứng viên lửa |
| `MAX_DIST` | 80 pixel | Khoảng cách tâm tối đa để ghép phát hiện vào ứng viên |
| `ALARM_AUTO_OFF_SECONDS` | 30 giây | Tắt trạng thái báo động khi đã qua thời gian này kể từ lần xác nhận lửa cuối |
| `LOG_COOLDOWN_SECONDS` | 10 giây | Khoảng nghỉ giữa các log bắt đầu báo cháy tự động |
| `MAX_LOG_ENTRIES` | 30 | Số log giữ trong bộ nhớ |

Ứng viên bị loại khi không được cập nhật trong 1,5 giây. Bộ đếm `hits` tích lũy theo các phát hiện khớp, không bắt buộc phải là 8 khung hình liên tiếp. Trong quá trình có lửa được xác nhận, lệnh báo cháy được gửi lại sau mỗi 30 giây và servo đổi mục tiêu sau mỗi 3 giây. Các bộ đếm thời gian này được xử lý khi có khung hình đi qua `process_logic()`.

Góc servo trong **`detector.py` mặc định** được suy ra từ tâm lửa với hệ số góc nhìn ngang đang đặt là `105`:

```text
angle = -round(((center_x - frame_width / 2) / frame_width) * 105, 1)
```

Đây là độ lệch ngang ước lượng từ ảnh; cần hiệu chỉnh theo camera và cơ cấu servo thực tế. Mã Python gửi góc quay và lệnh âm thanh, chưa có lệnh điều khiển bơm nước riêng.

**Các biến thể chưa thống nhất chiều góc:** `detector_416.py`, `detector_224.py` và `detector_tflite.py` dùng cùng biểu thức nhưng **không có dấu trừ ở đầu**. Vì vậy đổi từ bản 640 sang bản 416 sẽ đảo dấu góc với cùng vị trí lửa. Cần kiểm tra chiều quay thực tế trước khi đổi mô hình.

### Thông tin đọc trực tiếp từ mô hình ONNX

| Tệp | Dung lượng gần đúng (MiB) | Input `images` | Output `output0` | Phiên bản exporter trong metadata |
| --- | --- | --- | --- | --- |
| `best.onnx` | 5.88 | `[1, 3, 640, 640]`, float32 | `[1, 6, 8400]`, float32 | Ultralytics 8.4.71 |
| `best_416.onnx` | 11.63 | `[1, 3, 416, 416]`, float32 | `[1, 6, 3549]`, float32 | Ultralytics 8.4.75 |
| `best_224.onnx` | 11.56 | `[1, 3, 224, 224]`, float32 | `[1, 6, 1029]`, float32 | Ultralytics 8.4.72 |

Metadata cả ba mô hình xác nhận lớp `0: smoke`, `1: fire`, task `detect`, batch 1 và `nms=False`. Mã Python đọc 4 giá trị hộp `[cx, cy, w, h]` rồi hai điểm lớp từ output, đưa hộp về tọa độ ảnh gốc và tự chạy NMS.

### Thông tin đọc trực tiếp từ mô hình TFLite

| Tệp | Dung lượng gần đúng (MiB) | Input `images` | Output `Identity` |
| --- | --- | --- | --- |
| `best_float16.tflite` | 5.81 | `[1, 224, 224, 3]`, float32 | `[1, 6, 1029]`, float32 |
| `best_int8.tflite` | 3.19 | `[1, 640, 640, 3]`, float32 | `[1, 6, 8400]`, float32 |

Shape và dtype trong bảng được đọc từ schema của chính hai tệp. Tên `float16`/`int8` không có nghĩa tensor input/output cũng dùng dtype đó: cả hai tệp hiện có tensor vào/ra float32 và không có scale/zero-point ở các tensor này.

`FireDetectorTFLite` tự nhận biết NHWC/NCHW nhưng đặt `model_size = 224` và dùng giá trị đó để resize lẫn quy đổi tọa độ. Để thử `best_int8.tflite` hiện có, cần chỉnh kích thước xử lý theo input 640×640, đồng thời kiểm tra quy ước tọa độ output; project chưa có kết quả xác nhận độ chính xác của nhánh này.

Tên biến thể 224/416 thể hiện kích thước đầu vào, không chứng minh tệp nhỏ hơn bản 640 hoặc tốc độ/độ chính xác tốt hơn. Chưa có benchmark trong project. Metadata không đủ để khẳng định một phiên bản kiến trúc YOLO cụ thể hoặc nguồn dataset công khai.

MOG2 dùng `history=300`, `varThreshold=25`, `detectShadows=False`. Diện tích contour tối thiểu lần lượt là 500, 200 và 100 pixel cho bản 640, 416 và 224/TFLite. Danh sách vùng được khử trùng bằng `set` rồi lấy tối đa 3 phần tử; không có sắp xếp theo độ mạnh chuyển động và không bảo đảm giữ vùng toàn cảnh khi có nhiều vùng.

Tiền xử lý resize trực tiếp, không letterbox. NMS hiện gộp hộp của cả hai lớp trong một lần gọi, không lọc riêng từng lớp.

## 7. Giao tiếp I2C và firmware ESP32

`hardware.py` chuyển chuỗi thành các byte ASCII và gửi bằng `write_i2c_block_data(address, 0, data)` trên bus 1, địa chỉ `0x08`.

| Lệnh | Xử lý trong `speaker/speaker.ino` |
| --- | --- |
| `MOVE:<angle>` | Cộng góc lệch vào 90°, giới hạn 20°–160° rồi gọi `servoX.write()` |
| `ALARM:1` | `myDFPlayer.playFolder(1, 1)`, bài báo cháy trong thư mục 01 |
| `ALARM:2` | `myDFPlayer.playFolder(1, 2)`, bài an toàn trong thư mục 01 |
| `ALARM:<number>` | `myDFPlayer.playFolder(1, number)`, bài được chọn khi test |

Firmware đã bỏ các byte `0` trong `receiveEvent()`, lưu chuỗi nhận được và đặt cờ để xử lý trong `loop()`. Python hiện không đọc phản hồi hay xác nhận thao tác từ ESP32; API báo thành công không chứng minh phần cứng đã thực hiện lệnh, vì lỗi gửi I2C được ghi log bên trong bộ điều khiển.

### Chân và thông số trong firmware tích hợp

| Tín hiệu | Chân/cấu hình ESP32 | Kết nối tương ứng |
| --- | --- | --- |
| I2C SDA | GPIO 21 | SDA của bus I2C Raspberry Pi |
| I2C SCL | GPIO 22 | SCL của bus I2C Raspberry Pi |
| I2C slave | `0x08`, khởi tạo với 100000 Hz | Master Python dùng bus 1, địa chỉ `0x08` |
| Servo | GPIO 18, PWM 50 Hz, xung attach 500–2400 µs | Dây tín hiệu servo |
| UART DFPlayer RX | GPIO 16 | TX của DFPlayer |
| UART DFPlayer TX | GPIO 17 | RX của DFPlayer |
| UART DFPlayer | `HardwareSerial(1)`, 9600 baud, 8N1 | Cổng điều khiển DFPlayer |
| LED | GPIO 2 | Chỉ thị khởi động/thử thẻ nhớ theo mã |
| Serial debug | 115200 baud | Serial Monitor |

Các thiết bị phải có GND chung và nguồn đúng thông số của phần cứng được chọn. Project chưa chỉ định model servo, công suất nguồn, mạch nguồn hoặc chân cấp nguồn; cần hoàn thiện phần này theo mạch thực tế. Bảng trên mô tả tín hiệu trong mã, không thay thế sơ đồ mạch đã kiểm chứng.

Ví dụ `MOVE:-25.5` được firmware đổi thành mục tiêu `64.5°` trước khi truyền vào API servo; `MOVE:0` đưa servo về tâm 90°. `ALARM:2` **phát bài an toàn**, không phải lệnh dừng phát âm thanh. Không có nhánh lệnh điều khiển bơm/relay trong firmware.

### Nạp firmware bằng Arduino IDE

1. Cài hỗ trợ board ESP32 trong Arduino IDE và chọn đúng board/cổng của thiết bị.
2. Cài hai thư viện được include trong mã: **ESP32Servo** và **DFRobotDFPlayerMini**. `Wire` và `HardwareSerial` thuộc môi trường ESP32.
3. Mở `speaker/speaker.ino`, đối chiếu các chân với mạch rồi biên dịch và nạp lên ESP32.
4. Chuẩn bị thẻ nhớ DFPlayer với cấu trúc âm thanh phù hợp lời gọi `playFolder(1, code)`:

   ```text
   01/
   ├── 001.mp3    # Nội dung báo cháy
   ├── 002.mp3    # Nội dung an toàn
   └── 003.mp3    # Bài thử thứ ba khi khởi động
   ```

5. Mở Serial Monitor ở 115200 baud để theo dõi khởi động, I2C và lệnh nhận được.
6. Đợi firmware in `Kiem tra hoan tat! Vao nhiem vu chinh.` rồi thử lệnh từ web.

Các tệp âm thanh chưa có trong repository. Firmware khởi tạo I2C trước, sau đó tự quay servo lần lượt **20° → 160° → 90°**, khởi tạo DFPlayer, đặt volume **25**, thử đọc số tệp tối đa **5 lần** và nếu đọc được thẻ thì phát bài 1–3, cách nhau 2,5 giây. DFPlayer khởi tạo lỗi chỉ được ghi log; chương trình vẫn tiếp tục. Sketch không có hàng đợi lệnh: trong thời gian khởi động hoặc khi lệnh đến nhanh, biến chứa lệnh có thể bị ghi đè.

### Vai trò của `servo/servo.ino`

Mặc dù có tên `servo`, sketch này **không điều khiển servo và không nhận I2C**. Nó mở UART2 ở 9600 baud, RX GPIO 16/TX GPIO 17, rồi gửi `ALARM:1` bằng `Serial2.println()` mỗi **20 giây** (`delay(20000)`; chú thích 2 giây trong tệp không khớp mã). Đây là chương trình thử giao tiếp UART riêng.

`speaker/speaker.ino` hiện nhận lệnh điều khiển từ I2C và dùng UART1 để nối DFPlayer; không có vòng đọc lệnh từ UART của sketch thử. Vì vậy không thể dùng `servo.ino` thay thế firmware tích hợp hoặc coi hai sketch là cặp truyền/nhận đã nối sẵn.

## 8. Các endpoint và cấu trúc dữ liệu

Các API POST dưới đây dùng trường form, trừ `/upload_test` dùng `multipart/form-data`. Giao diện camera trả HTML/chuyển hướng; các API trạng thái và điều khiển trả JSON.

| Phương thức | Đường dẫn | Chức năng / dữ liệu |
| --- | --- | --- |
| GET | `/` | Giao diện giám sát |
| GET | `/video_feed/<cam_id>` | Video camera dạng MJPEG |
| GET | `/api/status/<cam_id>` | Trạng thái của camera đang hoạt động; dùng `test` cho test tệp |
| POST | `/api/dismiss/<cam_id>` | Xóa báo động, gửi lệnh an toàn nếu không test phần cứng |
| POST | `/save_camera` | `cam_id`, `cam_name`, `config_mode`; thêm `cam_ip`, `cam_user`, `cam_pass` hoặc `cam_url_manual` |
| POST | `/delete_camera/<cam_id>` | Xóa cấu hình camera |
| POST | `/api/camera/resolution/<cam_id>` | `quality=hd` hoặc `quality=sd` |
| POST | `/upload_test` | Tải ảnh/video qua trường `file` |
| GET | `/stream_test/<filename>` | Video tải lên dạng MJPEG |
| POST | `/api/toggle_hw_test` | Đảo trạng thái test phần cứng |
| POST | `/api/test_servo` | `angle`: góc quay dạng số |
| POST | `/api/test_audio` | `filename`: số bài âm thanh |
| POST | `/api/simulate_alarm` | `state=danger` hoặc `state=safe` |

### Cấu hình camera cục bộ

Ví dụ minh họa cấu trúc `cameras.json`, chỉ dùng giá trị giả:

```json
{
  "cam_example": {
    "name": "Khu vực demo",
    "mode": "auto",
    "url": "rtsp://CAMERA_USER:CAMERA_PASSWORD@CAMERA_IP:554/stream1",
    "ip": "CAMERA_IP",
    "user": "CAMERA_USER",
    "password": "CAMERA_PASSWORD",
    "quality": "hd"
  },
  "cam_manual": {
    "name": "Camera nhập tay",
    "mode": "manual",
    "url": "rtsp://CAMERA_IP:554/CAMERA_STREAM"
  }
}
```

ID camera mới được tạo bằng `cam_<Unix_timestamp_giây>`. Lưu camera ở chế độ tự động đặt lại chất lượng thành `hd`. Nếu JSON không đọc được, `load_cameras()` trả danh sách rỗng mà không ghi lỗi. Cấu hình cục bộ hiện có thể chứa thêm `tapo_email` và `tapo_password`; ứng dụng Flask không sử dụng hai trường này.

`test_tapo.py` là script độc lập import `Tapo` từ `pytapo`, đăng nhập bằng thông tin hardcode rồi gọi `moveMotor(15, 0)`. Giao diện chính chưa có điều khiển quay camera qua pytapo; thanh zoom chỉ phóng ảnh trong trình duyệt.

### Trạng thái hệ thống

Khi `cam_id` yêu cầu trùng luồng đang hoạt động, `/api/status/<cam_id>` trả toàn bộ `system_status`:

| Trường | Nội dung |
| --- | --- |
| `cam_id` | Camera hiện tại; `test` khi thử tệp, `RELOAD` khi đổi luồng |
| `alarm` | Cờ báo động |
| `has_captured` | Đã chụp ảnh trong đợt báo động này hay chưa |
| `locations` | Vị trí lửa đã xác nhận, mỗi phần tử có `x`, `y`, `angle` |
| `logs` | Log mới nhất trước, giới hạn 30 dòng |
| `candidates` | Ứng viên gồm `x`, `y`, `angle`, `hits`, `last_time` |
| `last_log_time` | Mốc kiểm soát khoảng nghỉ ghi log |
| `last_fire_time` | Mốc xác nhận lửa gần nhất |
| `last_alarm_sound_time` | Mốc gửi lại lệnh âm thanh |
| `last_servo_time` | Mốc đổi mục tiêu servo |
| `servo_index` | Chỉ số mục tiêu servo tiếp theo |
| `hw_test_mode` | Chế độ thử phần cứng |

Nếu ID không khớp, API chỉ trả `{"alarm": false, "locations": [], "logs": []}`. `force_dismiss()` xóa báo động, ứng viên, cờ chụp và chỉ số servo, nhưng không xóa mọi mốc thời gian hoặc `locations` ngay lập tức. Nút tắt còi không khóa nhận diện: nếu lửa còn xuất hiện, hệ thống có thể xác nhận và báo lại.

## 9. Phạm vi và giới hạn hiện tại

- Hệ thống dùng trạng thái toàn cục cho **một luồng camera/test đang hoạt động**, chưa có giám sát nhiều camera độc lập đồng thời. Nhiều phiên xem có thể tác động đến cùng trạng thái.
- AI và logic điều khiển chạy theo yêu cầu stream MJPEG của trình duyệt, chưa có tiến trình giám sát nền độc lập khi đóng giao diện.
- Log chỉ tồn tại trong RAM, không có cơ sở dữ liệu hoặc lưu lịch sử lâu dài. Ảnh cảnh báo tự động chỉ được chụp trong luồng camera trực tiếp.
- Âm thanh web lấy từ URL bên ngoài trong `index.html`, cần mạng để tải và có thể cần thao tác người dùng để trình duyệt cho phép phát.
- Chưa có đăng nhập, phân quyền hoặc HTTPS; cấu hình camera được lưu dạng văn bản và đưa vào dữ liệu giao diện để sửa camera. Chỉ triển khai bản hiện tại trong mạng nội bộ tin cậy.
- Chưa có bộ kiểm thử tự động, số liệu độ chính xác/FPS được kiểm chứng hoặc hướng dẫn tái tạo quá trình huấn luyện.
- Khi video test kết thúc hoặc camera mất kết nối, logic theo khung hình có thể ngừng chạy; trạng thái báo động không tự hết chỉ nhờ thời gian trôi qua. Có thể dùng **TẮT CÒI** hoặc **AN TOÀN** để đặt lại trạng thái.
- Chuyển camera trực tiếp chưa chủ động xóa mọi ứng viên, trạng thái và nền MOG2; các dữ liệu theo dõi dùng chung có thể ảnh hưởng lúc chuyển nguồn.
- Upload chưa giới hạn dung lượng và chưa xử lý đầy đủ tên tệp rỗng, thiếu phần mở rộng hoặc ảnh hỏng. Tệp cùng tên có thể ghi đè nhau; không có cơ chế tự dọn thư mục upload.
- Firmware ESP32 không phản hồi trạng thái servo/DFPlayer về Raspberry Pi và không có hàng đợi lệnh hay xác nhận nhận lệnh.

## 10. Chuẩn bị đưa lên GitHub

`.gitignore` loại môi trường ảo, cache Python, cấu hình camera, script test có thông tin đăng nhập, tệp upload, ảnh cảnh báo và tệp IDE/hệ điều hành. Mô hình AI và mã nguồn ứng dụng vẫn được giữ.

**`cameras.json` và `test_tapo.py` cục bộ hiện có thông tin đăng nhập.** Không đưa chúng lên repository công khai. Để chia sẻ script thử Tapo, hãy bỏ thông tin đăng nhập hardcode và đọc từ cấu hình cục bộ/biến môi trường trước khi bỏ quy tắc ignore cho tệp đó. Script này dùng `pytapo`, không được gọi bởi ứng dụng Flask.

Thư mục hiện chưa được khởi tạo Git. Sau khi tạo một repository trống trên GitHub, có thể chạy:

```bash
git init
git add .
git status --short
git diff --cached --stat
git diff --cached
git commit -m "Initial commit: AI fire detection and ESP32 control"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repository>.git
git push -u origin main
```

Kiểm tra nội dung chuẩn bị commit trước khi chạy `git commit`. `.gitignore` chỉ ngăn thêm tệp chưa được theo dõi; nếu thông tin đăng nhập đã được commit/push ở nơi khác, cần thay mật khẩu và xử lý lịch sử Git tương ứng.

Project chưa có tệp `LICENSE` riêng cho mã nguồn. Metadata ba mô hình ONNX có trường `license` ghi **AGPL-3.0** của Ultralytics; cần giữ thông tin nguồn gốc này khi mô tả mô hình. Repository chưa có hồ sơ nguồn dữ liệu hoặc giấy phép dataset.

## 11. Quy trình kiểm tra hệ thống

Đây là các bước kiểm tra thủ công đề xuất theo hành vi mã nguồn, không phải báo cáo các thử nghiệm phần cứng đã chạy.

1. **Nạp và khởi động ESP32:** nạp `speaker/speaker.ino`, kiểm tra chuỗi khởi động ở Serial Monitor, chuyển động servo và ba bài âm thanh thử. Đợi hoàn tất `setup()`.
2. **Khởi động backend:** chạy `python app.py`; xác nhận mô hình được nạp. Khi dùng phần cứng, kiểm tra log `I2C Master Connected on Bus 1, Target ESP32: 0x8`.
3. **Thử AI bằng ảnh:** tải một ảnh hợp lệ, kiểm tra khung `smoke`/`fire` và ảnh `out_<tên_gốc>` trong `static/uploads/`. Nhánh ảnh không gửi lệnh servo tự động.
4. **Thử camera:** thêm camera, xem video và đổi HD/SD. Nút zoom chỉ thay đổi ảnh hiển thị, không đổi góc nhìn vật lý hoặc tọa độ suy luận.
5. **Thử servo thủ công:** bật test phần cứng trong luồng camera, gửi góc `0`, `-20`, `20`; mục tiêu trong firmware lần lượt là 90°, 70°, 110°. Đối chiếu Serial Monitor và hướng quay thực tế.
6. **Thử âm thanh:** gửi bài `001` và `002`; kiểm tra đúng nội dung trong thư mục 01 của thẻ nhớ.
7. **Thử mô phỏng:** chọn **BÁO CHÁY**, kiểm tra `ALARM:1`; chọn **AN TOÀN**, kiểm tra `ALARM:2` và trạng thái web. Trong chế độ test phần cứng, JavaScript bỏ qua phần cập nhật hiển thị alarm và log.
8. **Thử chu trình tự động bằng video có sẵn:** bật lại AI, tải video để kiểm tra tích lũy ứng viên, xác nhận lửa, lần lượt gửi góc và lặp âm thanh khi còn lửa. Kiểm tra chiều góc riêng khi đổi detector 640/416.
9. **Thử chụp và hết báo động:** với camera trực tiếp, xác nhận ảnh `static/uploads/alerts/fire_<timestamp>.jpg`. Tiếp tục cung cấp khung không có lửa để logic có thể chạy hết thời gian chờ và gửi bài an toàn.

Không cần tạo lửa thật để thử các nhánh phần mềm: có thể dùng ảnh, video mẫu và các nút mô phỏng hiện có.

## 12. Xử lý lỗi thường gặp

| Hiện tượng | Điểm cần kiểm tra |
| --- | --- |
| `ModuleNotFoundError` | Kích hoạt đúng môi trường ảo, cài các gói trong mục 4; `smbus2` vẫn cần được import dù không nối phần cứng |
| Không tìm thấy mô hình | Chạy từ thư mục chứa `app.py` và đúng tệp `best.onnx`/`best_416.onnx`; kiểm tra biến `USE_MODEL_416` |
| Lỗi shape/dtype khi thay model | So sánh input/output mô hình với resize, layout và dtype của detector; nhánh TFLite đang cố định kích thước 224 |
| Không mở được RTSP | Kiểm tra IP, đường mạng, tài khoản RTSP, mật khẩu và đường dẫn stream; backend tự thử kết nối lại sau lỗi đọc |
| Video trễ hoặc xử lý chậm | Thử SD, bản ONNX 416 hoặc tắt `use_sahi` khi khởi tạo detector; đo lại trên thiết bị vì chưa có benchmark |
| Không thấy cập nhật mô phỏng | Chọn một camera hoặc mở một tệp test trước để `currentCamId` có giá trị; kiểm tra chế độ test phần cứng |
| Không nghe còi web | Tương tác với trang để cho phép phát, kiểm tra mạng tới URL âm thanh và âm lượng trình duyệt |
| Báo không mở được I2C | Kiểm tra I2C đã bật, bus 1 tồn tại và tài khoản chạy có quyền mở thiết bị I2C |
| Có log gửi nhưng ESP32 không phản ứng | Kiểm tra SDA/SCL, GND, địa chỉ `0x08`, đúng sketch `speaker.ino`, trạng thái hoàn tất khởi động và log nhận trên Serial Monitor |
| Servo quay ngược khi đổi mô hình | Bản 640 đảo dấu góc, các bản 416/224/TFLite không đảo dấu; hiệu chỉnh theo cơ cấu thực tế |
| DFPlayer không phát đúng bài | Kiểm tra UART RX/TX, thẻ nhớ, thư mục 01, số bài và log khởi tạo DFPlayer |
| Tắt còi rồi báo lại | Lửa còn được detector nhận diện; thao tác tắt chỉ reset trạng thái, không vô hiệu hóa AI |
| Video đã hết nhưng vẫn báo động | Không còn khung chạy `process_logic()` để tính hết thời gian chờ; dùng nút đặt trạng thái an toàn |

Các hướng xử lý trên dựa vào mã hiện có. Phiên bản Arduino IDE, ESP32 core và hai thư viện firmware chưa được khóa trong project; cần ghi lại môi trường đã biên dịch thành công khi triển khai thực tế.
