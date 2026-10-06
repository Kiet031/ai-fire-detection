# Import thư viện smbus2: Đây là thư viện cốt lõi trên Raspberry Pi dùng để giao tiếp qua chuẩn I2C (Inter-Integrated Circuit)
import smbus2
# Import thư viện time: Dùng để xử lý các độ trễ (delay) hoặc tính toán thời gian nếu cần thiết
import time

# =====================================================================
# CLASS ĐIỀU KHIỂN PHẦN CỨNG ESP32 (I2C MASTER)
# Nhiệm vụ: Giúp Raspberry Pi đóng vai trò là Master (Chủ), chủ động 
# kết nối và đẩy lệnh điều khiển xuống ESP32 (Slave) qua 2 dây SDA/SCL.
# =====================================================================
class ESP32Controller:
    # -----------------------------------------------------------------
    # HÀM KHỞI TẠO (__init__): Thiết lập thông số I2C khi hệ thống vừa bật
    # -----------------------------------------------------------------
    def __init__(self, i2c_bus=1, i2c_address=0x08):
        # Lưu lại địa chỉ I2C của ESP32 (Mặc định là 0x08, phải khớp tuyệt đối với mã C++ trên ESP32)
        self.address = i2c_address
        try:
            # Khởi tạo đối tượng SMBus trên bus số 1 (Bus I2C mặc định của Raspberry Pi)
            self.bus = smbus2.SMBus(i2c_bus)
            print(f"[*] I2C Master Connected on Bus {i2c_bus}, Target ESP32: {hex(i2c_address)}")
        except Exception as e:
            # Bắt lỗi nếu cổng I2C chưa được mở trong phần cấu hình của hệ điều hành Linux (Raspberry OS)
            self.bus = None
            print(f"[!] Lỗi khởi tạo I2C: {e}")
            print("Gợi ý: Chạy lệnh 'sudo raspi-config' -> Interface Options -> Bật I2C")

    # -----------------------------------------------------------------
    # HÀM TRUYỀN DỮ LIỆU CỐT LÕI (send_command)
    # Nhiệm vụ: Dịch chuỗi chữ (String) của máy tính thành mã máy (Byte Array) 
    # để có thể truyền tín hiệu điện vật lý qua chuẩn I2C.
    # -----------------------------------------------------------------
    def send_command(self, msg):
        """Hàm dùng chung để đóng gói và gửi chuỗi lệnh qua I2C"""
        # Chỉ thực hiện gửi dữ liệu nếu bus I2C đã được khởi tạo thành công
        if self.bus:
            try:
                # Chuyển chuỗi văn bản (VD: "ALARM:1") thành mảng các số nguyên (byte)
                # Hàm ord(c) sẽ lấy giá trị mã ASCII của từng ký tự 'c' trong chuỗi 'msg'
                data = [ord(c) for c in msg]
                
                # Lệnh gửi khối dữ liệu (Block Data) qua giao thức I2C tới địa chỉ của ESP32
                # Tham số 0 ở giữa là mã thanh ghi (register), ta để 0 vì đang truyền chuỗi tự do
                self.bus.write_i2c_block_data(self.address, 0, data)
                print(f"[DEBUG I2C] -> Đã gửi cho ESP32: {msg}")
            except Exception as e:
                # Bắt lỗi nếu đường truyền vật lý bị đứt, lỏng dây, hoặc ESP32 mất nguồn
                print(f"[DEBUG I2C] ❌ Lỗi truyền I2C '{msg}': {e}")
        else:
            # Cảnh báo nếu chưa mở bus I2C trên hệ thống
            print(f"[DEBUG I2C] ❌ Không thể gửi '{msg}' vì cổng I2C chưa mở!")

    # -----------------------------------------------------------------
    # HÀM ĐIỀU KHIỂN ĐỘNG CƠ SERVO (move_servo)
    # Nhiệm vụ: Gói thông số góc quay của AI vào cú pháp chuẩn "MOVE:góc" (VD: "MOVE:25.5")
    # -----------------------------------------------------------------
    def move_servo(self, angle):
        self.send_command(f"MOVE:{angle}")

    # -----------------------------------------------------------------
    # HÀM ĐIỀU KHIỂN CÒI HÚ DFPLAYER (trigger_alarm)
    # Nhiệm vụ: Gói mã hiệu lệnh vào cú pháp chuẩn "ALARM:mã" (VD: "ALARM:1" báo cháy, "ALARM:2" an toàn)
    # -----------------------------------------------------------------
    def trigger_alarm(self, code):
        self.send_command(f"ALARM:{code}")